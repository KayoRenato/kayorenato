"""
Product normalization using the Claude API.

Takes raw item descriptions extracted from NFC-e receipts (e.g.
"ACUCAR UNIAO KG 5X1KG", "OLEO SOJA LIZA 900ML") and returns structured
product metadata: canonical name, brand, category, and a base-unit price
suitable for cross-store / cross-time comparison.
"""

import json
import logging
import re
from decimal import Decimal
from typing import Optional

import anthropic

from receipt_analyzer.models.receipt import RawItem, NormalizedItem

log = logging.getLogger(__name__)

# Base units we normalize everything into for comparison
_BASE_UNIT_MAP = {
    # mass
    "KG": ("kg", Decimal("1")),
    "G": ("kg", Decimal("0.001")),
    "GR": ("kg", Decimal("0.001")),
    "MG": ("kg", Decimal("0.000001")),
    # volume
    "L": ("L", Decimal("1")),
    "LT": ("L", Decimal("1")),
    "ML": ("L", Decimal("0.001")),
    # count
    "UN": ("un", Decimal("1")),
    "PC": ("un", Decimal("1")),
    "CX": ("un", Decimal("1")),
    "PCT": ("un", Decimal("1")),
    "FD": ("un", Decimal("1")),
    "DZ": ("un", Decimal("12")),
}

_SYSTEM_PROMPT = """\
You are a product data extractor for a Brazilian supermarket price-comparison app.
Given a list of raw product descriptions from NFC-e receipts (in Portuguese),
extract structured information for each item.

Return a JSON array — one object per item — with these fields:
  - "canonical_name": short, clean product name in Portuguese, no brand, no size
  - "brand": brand name if identifiable, else null
  - "category": one of [Laticínios, Bebidas, Carnes, Hortifruti, Padaria,
      Mercearia, Limpeza, Higiene, Congelados, Outros]
  - "base_unit": the unit the price_per_base_unit should be expressed in.
      Use "kg" for solid foods sold by weight, "L" for liquids, "un" for
      discrete items (packages, units, boxes).
  - "base_quantity": total amount in base_unit for this line item
      (e.g. if the receipt shows 2 packs of 500g each → 1.0 kg)

Rules:
- canonical_name must be generic enough to match the same product across stores
  (e.g. "Açúcar Cristal" not "AÇÚCAR UNIÃO CRISTAL 5KG")
- For multi-packs like "5X1KG" or "6X1L", multiply out the base_quantity
- Return ONLY the JSON array, no markdown, no explanation
"""


def _build_user_message(items: list[RawItem]) -> str:
    rows = []
    for i, item in enumerate(items):
        rows.append(
            f'{i}: description="{item.description}" '
            f'qty={item.quantity} unit="{item.unit}" '
            f'unit_price={item.unit_price} total={item.total_price}'
        )
    return "\n".join(rows)


def _parse_response(text: str, items: list[RawItem]) -> list[dict]:
    # Strip markdown code fences if present
    text = re.sub(r"```(?:json)?", "", text).strip().rstrip("```").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        log.error("Claude returned invalid JSON: %s\nRaw: %s", exc, text[:500])
        return []
    if not isinstance(parsed, list):
        log.error("Expected JSON array from Claude, got %s", type(parsed))
        return []
    return parsed


def _compute_price_per_base_unit(
    item: RawItem,
    base_unit: str,
    base_quantity: Decimal,
) -> Decimal:
    if base_quantity == 0:
        return Decimal("0")
    return (item.total_price / base_quantity).quantize(Decimal("0.0001"))


def normalize(items: list[RawItem], api_key: Optional[str] = None) -> list[NormalizedItem]:
    """
    Normalize a list of RawItems using the Claude API.

    Args:
        items: Raw items from the NFC-e parser.
        api_key: Anthropic API key. Reads ANTHROPIC_API_KEY env var if None.

    Returns:
        List of NormalizedItems in the same order as input.
        If an item fails normalization it falls back to a minimal default.
    """
    if not items:
        return []

    client = anthropic.Anthropic(api_key=api_key)  # reads env if key is None

    response = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=4096,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_user_message(items)}],
    )

    raw_text = response.content[0].text
    enriched = _parse_response(raw_text, items)

    results: list[NormalizedItem] = []
    for i, item in enumerate(items):
        meta = enriched[i] if i < len(enriched) else {}

        raw_base_unit = str(meta.get("base_unit") or item.unit).upper()
        base_unit_info = _BASE_UNIT_MAP.get(raw_base_unit, ("un", Decimal("1")))
        base_unit = base_unit_info[0]

        # base_quantity from Claude (expressed in base_unit already)
        try:
            base_quantity = Decimal(str(meta.get("base_quantity") or item.quantity))
        except Exception:
            base_quantity = item.quantity * base_unit_info[1]

        price_per_base = _compute_price_per_base_unit(item, base_unit, base_quantity)

        results.append(NormalizedItem(
            raw_description=item.description,
            canonical_name=meta.get("canonical_name") or item.description,
            brand=meta.get("brand") or None,
            category=meta.get("category") or "Outros",
            quantity=item.quantity,
            unit=item.unit,
            unit_price=item.unit_price,
            total_price=item.total_price,
            base_unit=base_unit,
            base_quantity=base_quantity,
            price_per_base_unit=price_per_base,
        ))

    return results
