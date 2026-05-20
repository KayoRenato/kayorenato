"""
Unit tests for the HTML parser using fixture HTML snippets.

These tests run without network access or a real SEFAZ portal — they use
representative HTML fragments modeled after the actual SEFAZ-PE NFC-e viewer.
"""

from decimal import Decimal
from datetime import datetime

from receipt_analyzer.scraper.parser import parse_receipt_html, _to_decimal


# ---------------------------------------------------------------------------
# Minimal HTML fixture — mirrors the structure of the SEFAZ-PE NFC-e viewer
# ---------------------------------------------------------------------------

FIXTURE_HTML = """
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"/></head>
<body>
  <div id="infos">
    <span>SUPERMERCADO NOVO LTDA</span><br/>
    CNPJ: 02.030.015/7004-056<br/>
    RUA DAS FLORES, 123 - RECIFE - PE
  </div>

  <div id="NFe">
    <span>NFC-e n. 000043775 Série 113 18/11/2025 20:56:06</span>
  </div>

  <div id="totalNota">
    Valor Total R$ 199,80
  </div>

  <table id="myTable">
    <tr>
      <th>#</th><th>Descrição</th><th>Qtde</th><th>Un</th>
      <th>Vl Unit</th><th>Vl Total</th>
    </tr>
    <tr>
      <td>1</td>
      <td>ACUCAR UNIAO CRISTAL KG 5X1KG</td>
      <td>1,000</td><td>PCT</td>
      <td>19,90</td><td>19,90</td>
    </tr>
    <tr>
      <td>2</td>
      <td>OLEO SOJA LIZA 900ML</td>
      <td>2,000</td><td>UN</td>
      <td>8,99</td><td>17,98</td>
    </tr>
    <tr>
      <td>3</td>
      <td>ARROZ TIOJOAO TIPO1 5KG</td>
      <td>1,000</td><td>PCT</td>
      <td>29,90</td><td>29,90</td>
    </tr>
    <tr>
      <td>4</td>
      <td>FEIJAO CARIOCA KICALDO 1KG</td>
      <td>3,000</td><td>KG</td>
      <td>8,50</td><td>25,50</td>
    </tr>
    <tr>
      <td>5</td>
      <td>LEITE INTEGRAL PARMALAT 1L CX</td>
      <td>6,000</td><td>UN</td>
      <td>5,49</td><td>32,94</td>
    </tr>
  </table>
</body>
</html>
"""


def test_to_decimal_standard():
    assert _to_decimal("1.234,56") == Decimal("1234.56")


def test_to_decimal_no_thousands():
    assert _to_decimal("19,90") == Decimal("19.90")


def test_to_decimal_integer():
    assert _to_decimal("100") == Decimal("100")


def test_parse_store():
    receipt = parse_receipt_html(FIXTURE_HTML, nfce_key="2" * 44)
    assert "SUPERMERCADO" in receipt.store.name.upper() or receipt.store.name != ""
    assert receipt.store.cnpj != ""
    assert receipt.store.state == "PE"
    assert "RECIFE" in receipt.store.city.upper()


def test_parse_datetime():
    receipt = parse_receipt_html(FIXTURE_HTML, nfce_key="2" * 44)
    assert receipt.issued_at.year == 2025
    assert receipt.issued_at.month == 11
    assert receipt.issued_at.day == 18


def test_parse_total():
    receipt = parse_receipt_html(FIXTURE_HTML, nfce_key="2" * 44)
    assert receipt.total == Decimal("199.80")


def test_parse_items_count():
    receipt = parse_receipt_html(FIXTURE_HTML, nfce_key="2" * 44)
    assert len(receipt.raw_items) == 5


def test_parse_item_fields():
    receipt = parse_receipt_html(FIXTURE_HTML, nfce_key="2" * 44)
    sugar = receipt.raw_items[0]
    assert "ACUCAR" in sugar.description
    assert sugar.quantity == Decimal("1.000")
    assert sugar.unit == "PCT"
    assert sugar.unit_price == Decimal("19.90")
    assert sugar.total_price == Decimal("19.90")


def test_parse_item_multi_qty():
    receipt = parse_receipt_html(FIXTURE_HTML, nfce_key="2" * 44)
    oil = receipt.raw_items[1]
    assert oil.quantity == Decimal("2.000")
    assert oil.total_price == Decimal("17.98")


def test_parse_nfce_key():
    key = "2" * 44
    receipt = parse_receipt_html(FIXTURE_HTML, nfce_key=key)
    assert receipt.nfce_key == key


def test_parse_source_url():
    url = "https://nfce.sefaz.pe.gov.br/nfce/consulta?p=test"
    receipt = parse_receipt_html(FIXTURE_HTML, nfce_key="2" * 44, source_url=url)
    assert receipt.source_url == url
