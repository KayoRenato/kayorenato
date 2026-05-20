"""
Scraper for Brazilian NFC-e receipts via SEFAZ state portals.

Supports extracting receipt data given either:
  - A full QR-code URL (e.g. https://nfce.sefaz.pe.gov.br/nfce/consulta?p=...)
  - A 44-digit access key (chave de acesso)

Currently targets SEFAZ-PE (Pernambuco).  The HTML structure is shared across
most states, so adding other UFs only requires a URL template per state.
"""

import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse, parse_qs

from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout
from bs4 import BeautifulSoup

from receipt_analyzer.models.receipt import RawItem, Store, Receipt
from receipt_analyzer.scraper.parser import parse_receipt_html


# URL templates keyed by 2-digit UF code embedded in the access key (positions 0-1)
_PORTAL_TEMPLATES: dict[str, str] = {
    "26": "https://nfce.sefaz.pe.gov.br/nfce/consulta?p={key}|2|1|1|{digest}",
}

# Fallback: some states use the access key directly without a digest
_PORTAL_SIMPLE: dict[str, str] = {
    "26": "https://nfce.sefaz.pe.gov.br/nfce/consulta?p={key}",
}


def _clean_key(raw: str) -> str:
    """Strip spaces/dashes from a 44-digit access key."""
    cleaned = re.sub(r"[\s\-]", "", raw)
    if len(cleaned) != 44 or not cleaned.isdigit():
        raise ValueError(
            f"Access key must be 44 digits after removing spaces/dashes, got {len(cleaned)}: {cleaned!r}"
        )
    return cleaned


def _uf_code(key: str) -> str:
    """Extract the 2-digit UF code from positions 20-21 of the access key."""
    # NF-e key layout: cUF(2) AAMM(4) CNPJ(14) mod(2) serie(3) nNF(9) tpEmis(1) cNF(8) cDV(1)
    return key[:2]


def _build_url_from_key(key: str) -> str:
    uf = _uf_code(key)
    template = _PORTAL_SIMPLE.get(uf)
    if not template:
        raise ValueError(f"No portal URL configured for UF code '{uf}'. Supported: {list(_PORTAL_SIMPLE)}")
    return template.format(key=key)


def _extract_key_from_url(url: str) -> str:
    """Pull the 44-digit key out of a SEFAZ QR-code URL."""
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    # Most states use ?p=KEY|... or ?chNFe=KEY or ?chave=KEY
    for param in ("p", "chNFe", "chave", "nfce"):
        if param in params:
            value = params[param][0]
            # The 'p' param is pipe-delimited: KEY|cUF|cTP|...
            candidate = value.split("|")[0]
            try:
                return _clean_key(candidate)
            except ValueError:
                continue
    raise ValueError(f"Could not extract access key from URL: {url}")


def _fetch_html(url: str, timeout_ms: int = 30_000) -> str:
    """Load the NFC-e consultation page and return its rendered HTML."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="pt-BR",
        )
        page: Page = context.new_page()
        try:
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            # Wait for the item table to appear — its id varies by state portal
            for selector in ("#myTable", "#tableItens", ".toItens", "table.toItens"):
                try:
                    page.wait_for_selector(selector, timeout=10_000)
                    break
                except PWTimeout:
                    continue
            html = page.content()
        finally:
            browser.close()
    return html


def scrape(source: str, timeout_ms: int = 30_000) -> Receipt:
    """
    Main entry point.

    Args:
        source: Either a 44-digit access key (spaces allowed) or a full QR-code URL.
        timeout_ms: Playwright page load timeout in milliseconds.

    Returns:
        A Receipt with raw_items populated.
    """
    if source.startswith("http"):
        url = source
        key = _extract_key_from_url(url)
    else:
        key = _clean_key(source)
        url = _build_url_from_key(key)

    html = _fetch_html(url, timeout_ms=timeout_ms)
    receipt = parse_receipt_html(html, nfce_key=key, source_url=url)
    return receipt
