"""
Unit tests for the normalizer — tested without real Claude API calls
by mocking the Anthropic client.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

from receipt_analyzer.models.receipt import RawItem
from receipt_analyzer.scraper.normalizer import normalize, _compute_price_per_base_unit, _BASE_UNIT_MAP


def _make_item(desc, qty, unit, unit_price, total):
    return RawItem(
        description=desc,
        quantity=Decimal(str(qty)),
        unit=unit,
        unit_price=Decimal(str(unit_price)),
        total_price=Decimal(str(total)),
    )


MOCK_CLAUDE_RESPONSE = [
    {
        "canonical_name": "Açúcar Cristal",
        "brand": "União",
        "category": "Mercearia",
        "base_unit": "kg",
        "base_quantity": 5.0,
    },
    {
        "canonical_name": "Óleo de Soja",
        "brand": "Liza",
        "category": "Mercearia",
        "base_unit": "L",
        "base_quantity": 1.8,
    },
]


@patch("receipt_analyzer.scraper.normalizer.anthropic.Anthropic")
def test_normalize_returns_correct_count(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="[]")]
    mock_client.messages.create.return_value = mock_response

    items = [_make_item("ACUCAR UNIAO 5KG", 1, "PCT", "19.90", "19.90")]
    result = normalize(items, api_key="test-key")
    assert len(result) == 1


@patch("receipt_analyzer.scraper.normalizer.anthropic.Anthropic")
def test_normalize_extracts_brand_and_category(mock_anthropic_cls):
    import json
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=json.dumps(MOCK_CLAUDE_RESPONSE))]
    mock_client.messages.create.return_value = mock_response

    items = [
        _make_item("ACUCAR UNIAO CRISTAL KG 5X1KG", 1, "PCT", "19.90", "19.90"),
        _make_item("OLEO SOJA LIZA 900ML", 2, "UN", "8.99", "17.98"),
    ]
    result = normalize(items, api_key="test-key")

    assert result[0].canonical_name == "Açúcar Cristal"
    assert result[0].brand == "União"
    assert result[0].category == "Mercearia"
    assert result[0].base_unit == "kg"
    assert result[0].base_quantity == Decimal("5.0")

    assert result[1].canonical_name == "Óleo de Soja"
    assert result[1].brand == "Liza"
    assert result[1].base_unit == "L"


@patch("receipt_analyzer.scraper.normalizer.anthropic.Anthropic")
def test_normalize_price_per_base_unit(mock_anthropic_cls):
    import json
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=json.dumps(MOCK_CLAUDE_RESPONSE))]
    mock_client.messages.create.return_value = mock_response

    items = [
        _make_item("ACUCAR UNIAO CRISTAL KG 5X1KG", 1, "PCT", "19.90", "19.90"),
        _make_item("OLEO SOJA LIZA 900ML", 2, "UN", "8.99", "17.98"),
    ]
    result = normalize(items, api_key="test-key")

    # 19.90 / 5.0 kg = 3.98 per kg
    assert result[0].price_per_base_unit == Decimal("3.9800")
    # 17.98 / 1.8 L ≈ 9.9889
    assert result[1].price_per_base_unit == Decimal("9.9889")


def test_normalize_empty_list():
    result = normalize([], api_key="test-key")
    assert result == []


def test_compute_price_per_base_unit_zero_quantity():
    item = _make_item("TEST", 1, "UN", "10.00", "10.00")
    result = _compute_price_per_base_unit(item, "un", Decimal("0"))
    assert result == Decimal("0")


@patch("receipt_analyzer.scraper.normalizer.anthropic.Anthropic")
def test_normalize_fallback_on_invalid_json(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="not valid json")]
    mock_client.messages.create.return_value = mock_response

    items = [_make_item("PRODUTO QUALQUER", 1, "UN", "5.00", "5.00")]
    result = normalize(items, api_key="test-key")
    # Should fallback gracefully
    assert len(result) == 1
    assert result[0].raw_description == "PRODUTO QUALQUER"
    assert result[0].category == "Outros"
