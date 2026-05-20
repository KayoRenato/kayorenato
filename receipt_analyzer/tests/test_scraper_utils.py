"""
Unit tests for URL/key utility functions in the scraper.
No network access required.
"""

import pytest
from receipt_analyzer.scraper.nfce_scraper import _clean_key, _extract_key_from_url, _build_url_from_key


VALID_KEY = "26251120300157004056651130000437751870662337"
VALID_KEY_SPACED = "2625 1120 3001 5700 4056 6511 3000 0437 7518 7066 2337"


def test_clean_key_removes_spaces():
    assert _clean_key(VALID_KEY_SPACED) == VALID_KEY


def test_clean_key_already_clean():
    assert _clean_key(VALID_KEY) == VALID_KEY


def test_clean_key_rejects_short():
    with pytest.raises(ValueError, match="44 digits"):
        _clean_key("1234")


def test_clean_key_rejects_non_digits():
    with pytest.raises(ValueError):
        _clean_key("A" * 44)


def test_extract_key_from_p_param():
    url = f"https://nfce.sefaz.pe.gov.br/nfce/consulta?p={VALID_KEY}|2|1|1|HASH"
    assert _extract_key_from_url(url) == VALID_KEY


def test_extract_key_from_url_no_extras():
    url = f"https://nfce.sefaz.pe.gov.br/nfce/consulta?p={VALID_KEY}"
    assert _extract_key_from_url(url) == VALID_KEY


def test_extract_key_raises_on_bad_url():
    with pytest.raises(ValueError):
        _extract_key_from_url("https://example.com/no-key-here")


def test_build_url_from_key_pe():
    url = _build_url_from_key(VALID_KEY)
    assert "nfce.sefaz.pe.gov.br" in url
    assert VALID_KEY in url


def test_build_url_raises_unknown_uf():
    # Key starting with "99" — no UF registered
    bad_key = "99" + "0" * 42
    with pytest.raises(ValueError, match="No portal URL configured"):
        _build_url_from_key(bad_key)
