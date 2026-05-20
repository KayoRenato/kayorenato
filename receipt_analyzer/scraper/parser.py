"""
HTML parser for SEFAZ NFC-e consultation pages.

The SEFAZ-PE portal (and most state portals using the same NF-e viewer)
renders items in a table with id="myTable" or class="toItens".
Store data appears in a div#infos or similar header block.

This parser is intentionally defensive: if a field can't be parsed it logs
the issue and uses a sensible default rather than crashing the whole receipt.
"""

import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from bs4 import BeautifulSoup, Tag

from receipt_analyzer.models.receipt import RawItem, Store, Receipt

log = logging.getLogger(__name__)


def _to_decimal(text: str) -> Decimal:
    """Convert Brazilian-formatted number string to Decimal ('1.234,56' → Decimal('1234.56'))."""
    cleaned = text.strip().replace(".", "").replace(",", ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"Cannot convert {text!r} to Decimal")


def _first_text(tag: Optional[Tag], default: str = "") -> str:
    if tag is None:
        return default
    return tag.get_text(separator=" ", strip=True)


def _parse_store(soup: BeautifulSoup) -> Store:
    # The emitter block varies across states but typically lives in #infos or .txtTopo
    name = ""
    cnpj = ""
    city = ""
    state = ""
    address = ""

    # Try common selectors
    emitter = (
        soup.find("div", id="infos")
        or soup.find("div", class_="txtTopo")
        or soup.find("div", id="collapse4")  # some states
    )

    if emitter:
        text = emitter.get_text("\n", strip=True)
        lines = [l.strip() for l in text.splitlines() if l.strip()]

        # First non-empty line is usually the store name
        if lines:
            name = lines[0]

        # CNPJ: 14 digits with standard mask
        cnpj_match = re.search(r"\d{2}[\.\s]?\d{3}[\.\s]?\d{3}[\/\s]?\d{4}[\-\s]?\d{2}", text)
        if cnpj_match:
            cnpj = re.sub(r"\D", "", cnpj_match.group())

        # City/State pattern "CIDADE - UF" or "CIDADE/UF"
        city_match = re.search(r"([A-ZÀ-Ú][A-ZÀ-Ú ]*[A-ZÀ-Ú])\s*[-/]\s*([A-Z]{2})\b", text)
        if city_match:
            city = city_match.group(1).strip().title()
            state = city_match.group(2).strip()

    return Store(name=name, cnpj=cnpj, city=city, state=state, address=address or None)


def _parse_datetime(soup: BeautifulSoup) -> datetime:
    # Look for emission date/time — format varies: "DD/MM/YYYY HH:MM:SS"
    text = soup.get_text()
    # Pattern: date and time on the NFC-e key line or a dedicated span
    dt_match = re.search(r"(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2}:\d{2})", text)
    if dt_match:
        try:
            return datetime.strptime(f"{dt_match.group(1)} {dt_match.group(2)}", "%d/%m/%Y %H:%M:%S")
        except ValueError:
            pass
    # Fallback: date only
    d_match = re.search(r"(\d{2}/\d{2}/\d{4})", text)
    if d_match:
        try:
            return datetime.strptime(d_match.group(1), "%d/%m/%Y")
        except ValueError:
            pass
    log.warning("Could not parse emission datetime from HTML")
    return datetime.min


def _parse_total(soup: BeautifulSoup) -> Decimal:
    # "Valor Total R$" label followed by the amount
    for pattern in [
        r"Valor\s+Total[^\d]*R\$?\s*([\d.,]+)",
        r"VALOR\s+TOTAL[^\d]*R\$?\s*([\d.,]+)",
        r"Total\s+da\s+Nota[^\d]*R\$?\s*([\d.,]+)",
    ]:
        match = re.search(pattern, soup.get_text(), re.IGNORECASE)
        if match:
            try:
                return _to_decimal(match.group(1))
            except ValueError:
                pass
    log.warning("Could not parse total from HTML")
    return Decimal("0")


def _parse_items(soup: BeautifulSoup) -> list[RawItem]:
    items: list[RawItem] = []

    # Try to find the items table — SEFAZ-PE uses id="myTable" or class="toItens"
    table = (
        soup.find("table", id="myTable")
        or soup.find("table", class_="toItens")
        or soup.find("table", id="tableItens")
    )

    if table is None:
        # Fallback: look for any table that has quantity/price columns
        for t in soup.find_all("table"):
            headers = t.find("tr")
            if headers and re.search(r"Qtde|Qtd\.|Quantidade", headers.get_text(), re.IGNORECASE):
                table = t
                break

    if table is None:
        log.warning("Could not find items table in HTML")
        return items

    rows = table.find_all("tr")
    # Skip header row(s)
    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if len(cells) < 4:
            continue
        texts = [c.get_text(strip=True) for c in cells]

        try:
            # Column order in SEFAZ-PE: Descrição | Qtde | Un | Vl Unit | Vl Total
            # Some portals insert a # (item number) as the first column
            offset = 0
            if re.match(r"^\d+$", texts[0]):
                offset = 1  # skip item number column

            description = texts[offset]
            if not description or description.lower() in ("descrição", "produto"):
                continue

            qty_text = texts[offset + 1]
            unit = texts[offset + 2] if len(texts) > offset + 2 else "un"
            unit_price_text = texts[offset + 3] if len(texts) > offset + 3 else "0"
            total_text = texts[offset + 4] if len(texts) > offset + 4 else "0"

            # Some portals embed unit inside the qty cell: "2,000 KG"
            unit_in_qty = re.match(r"([\d.,]+)\s+([A-Za-z]+)", qty_text)
            if unit_in_qty:
                qty_text = unit_in_qty.group(1)
                if unit in ("", "-"):
                    unit = unit_in_qty.group(2)

            items.append(RawItem(
                description=description,
                quantity=_to_decimal(qty_text),
                unit=unit.upper().strip(),
                unit_price=_to_decimal(unit_price_text),
                total_price=_to_decimal(total_text),
            ))
        except (ValueError, IndexError) as exc:
            log.debug("Skipping row %r: %s", texts, exc)
            continue

    return items


def parse_receipt_html(html: str, nfce_key: str, source_url: Optional[str] = None) -> Receipt:
    """Parse rendered SEFAZ NFC-e HTML into a Receipt with raw items."""
    soup = BeautifulSoup(html, "html.parser")

    store = _parse_store(soup)
    issued_at = _parse_datetime(soup)
    total = _parse_total(soup)
    raw_items = _parse_items(soup)

    return Receipt(
        nfce_key=nfce_key,
        store=store,
        issued_at=issued_at,
        total=total,
        raw_items=raw_items,
        source_url=source_url,
    )
