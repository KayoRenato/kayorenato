"""
Tests for the repository layer using an in-memory SQLite database.
No network, no files, no Claude API.
"""

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from receipt_analyzer.db.database import init_db
from receipt_analyzer.db.orm import Base, Store, Receipt as ORMReceipt, Product, ReceiptItem
from receipt_analyzer.db.repository import (
    get_or_create_store,
    get_or_create_product,
    save_receipt,
    save_normalized_items,
    get_price_history,
    compare_stores,
    get_basket_trend,
    list_products,
    list_receipts,
)
from receipt_analyzer.models.receipt import (
    Receipt as DomainReceipt,
    RawItem,
    NormalizedItem,
    Store as DomainStore,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as s:
        yield s


def _make_domain_receipt(
    key: str = "1" * 44,
    cnpj: str = "12345678000100",
    store_name: str = "Supermercado Teste",
    city: str = "Recife",
    state: str = "PE",
    issued_at: datetime = datetime(2025, 11, 18, 20, 56, 6),
    total: str = "199.80",
) -> DomainReceipt:
    return DomainReceipt(
        nfce_key=key,
        store=DomainStore(name=store_name, cnpj=cnpj, city=city, state=state),
        issued_at=issued_at,
        total=Decimal(total),
        raw_items=[
            RawItem("ACUCAR UNIAO 5KG", Decimal("1"), "PCT", Decimal("19.90"), Decimal("19.90")),
            RawItem("OLEO SOJA LIZA 900ML", Decimal("2"), "UN", Decimal("8.99"), Decimal("17.98")),
        ],
    )


def _make_normalized(items_count: int = 2) -> list[NormalizedItem]:
    sugar = NormalizedItem(
        raw_description="ACUCAR UNIAO 5KG",
        canonical_name="Açúcar Cristal",
        brand="União",
        category="Mercearia",
        quantity=Decimal("1"),
        unit="PCT",
        unit_price=Decimal("19.90"),
        total_price=Decimal("19.90"),
        base_unit="kg",
        base_quantity=Decimal("5.0"),
        price_per_base_unit=Decimal("3.9800"),
    )
    oil = NormalizedItem(
        raw_description="OLEO SOJA LIZA 900ML",
        canonical_name="Óleo de Soja",
        brand="Liza",
        category="Mercearia",
        quantity=Decimal("2"),
        unit="UN",
        unit_price=Decimal("8.99"),
        total_price=Decimal("17.98"),
        base_unit="L",
        base_quantity=Decimal("1.8"),
        price_per_base_unit=Decimal("9.9889"),
    )
    return [sugar, oil][:items_count]


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def test_get_or_create_store_creates(session):
    store = get_or_create_store(session, "12345678000100", "Loja A", "Recife", "PE")
    assert store.id is not None
    assert store.cnpj == "12345678000100"


def test_get_or_create_store_idempotent(session):
    s1 = get_or_create_store(session, "12345678000100", "Loja A", "Recife", "PE")
    s2 = get_or_create_store(session, "12345678000100", "Loja A", "Recife", "PE")
    assert s1.id == s2.id


# ---------------------------------------------------------------------------
# Product
# ---------------------------------------------------------------------------

def test_get_or_create_product_creates(session):
    p = get_or_create_product(session, "Açúcar Cristal", "União", "Mercearia", "kg")
    assert p.id is not None
    assert p.canonical_name == "Açúcar Cristal"
    assert p.brand == "União"


def test_get_or_create_product_idempotent(session):
    p1 = get_or_create_product(session, "Açúcar Cristal", "União", "Mercearia", "kg")
    p2 = get_or_create_product(session, "Açúcar Cristal", "União", "Mercearia", "kg")
    assert p1.id == p2.id


def test_get_or_create_product_null_brand(session):
    p1 = get_or_create_product(session, "Produto Genérico", None, "Outros", "un")
    p2 = get_or_create_product(session, "Produto Genérico", None, "Outros", "un")
    assert p1.id == p2.id


def test_different_brands_different_products(session):
    p1 = get_or_create_product(session, "Açúcar Cristal", "União", "Mercearia", "kg")
    p2 = get_or_create_product(session, "Açúcar Cristal", "Caravelas", "Mercearia", "kg")
    assert p1.id != p2.id


# ---------------------------------------------------------------------------
# save_receipt
# ---------------------------------------------------------------------------

def test_save_receipt_persists(session):
    domain = _make_domain_receipt()
    orm_r = save_receipt(session, domain)
    assert orm_r is not None
    assert orm_r.nfce_key == "1" * 44


def test_save_receipt_creates_items(session):
    domain = _make_domain_receipt()
    orm_r = save_receipt(session, domain)
    session.commit()
    items = orm_r.items
    assert len(items) == 2
    assert items[0].raw_description == "ACUCAR UNIAO 5KG"


def test_save_receipt_idempotent(session):
    domain = _make_domain_receipt()
    r1 = save_receipt(session, domain)
    r2 = save_receipt(session, domain)
    assert r1 is not None
    assert r2 is None  # duplicate returns None


def test_save_receipt_creates_store(session):
    domain = _make_domain_receipt(cnpj="99988877000100", store_name="Nova Loja")
    orm_r = save_receipt(session, domain)
    assert orm_r.store.cnpj == "99988877000100"


# ---------------------------------------------------------------------------
# save_normalized_items
# ---------------------------------------------------------------------------

def test_save_normalized_items_links_products(session):
    domain = _make_domain_receipt()
    orm_r = save_receipt(session, domain)
    save_normalized_items(session, orm_r, _make_normalized())
    session.commit()

    items = sorted(orm_r.items, key=lambda i: i.id)
    assert items[0].product is not None
    assert items[0].product.canonical_name == "Açúcar Cristal"
    assert items[0].price_per_base_unit == Decimal("3.9800")
    assert items[0].base_unit == "kg"


def test_save_normalized_items_creates_products(session):
    domain = _make_domain_receipt()
    orm_r = save_receipt(session, domain)
    save_normalized_items(session, orm_r, _make_normalized())
    session.commit()

    products = list_products(session)
    names = {p.canonical_name for p in products}
    assert "Açúcar Cristal" in names
    assert "Óleo de Soja" in names


# ---------------------------------------------------------------------------
# get_price_history
# ---------------------------------------------------------------------------

def _insert_two_receipts(session):
    """Helper: two receipts for the same store with the same sugar product."""
    for key, date, price, total in [
        ("1" * 44, datetime(2025, 11, 18), "3.9800", "199.80"),
        ("2" * 44, datetime(2026, 1, 21), "4.2000", "210.00"),
    ]:
        domain = _make_domain_receipt(key=key, issued_at=date, total=total)
        orm_r = save_receipt(session, domain)
        norm = [
            NormalizedItem(
                raw_description="ACUCAR UNIAO 5KG",
                canonical_name="Açúcar Cristal",
                brand="União",
                category="Mercearia",
                quantity=Decimal("1"),
                unit="PCT",
                unit_price=Decimal(price) * 5,
                total_price=Decimal(price) * 5,
                base_unit="kg",
                base_quantity=Decimal("5.0"),
                price_per_base_unit=Decimal(price),
            ),
            NormalizedItem(
                raw_description="OLEO SOJA LIZA 900ML",
                canonical_name="Óleo de Soja",
                brand="Liza",
                category="Mercearia",
                quantity=Decimal("2"),
                unit="UN",
                unit_price=Decimal("8.99"),
                total_price=Decimal("17.98"),
                base_unit="L",
                base_quantity=Decimal("1.8"),
                price_per_base_unit=Decimal("9.9889"),
            ),
        ]
        save_normalized_items(session, orm_r, norm)
    session.commit()


def test_get_price_history_returns_ordered(session):
    _insert_two_receipts(session)
    history = get_price_history(session, "Açúcar Cristal", brand="União")
    assert len(history) == 2
    assert history[0].date < history[1].date
    assert history[0].price_per_base_unit == Decimal("3.9800")
    assert history[1].price_per_base_unit == Decimal("4.2000")


def test_get_price_history_no_brand_filter(session):
    _insert_two_receipts(session)
    history = get_price_history(session, "Açúcar Cristal")
    assert len(history) == 2


def test_get_price_history_empty_for_unknown(session):
    _insert_two_receipts(session)
    history = get_price_history(session, "Produto Inexistente")
    assert history == []


# ---------------------------------------------------------------------------
# compare_stores
# ---------------------------------------------------------------------------

def test_compare_stores_two_stores(session):
    # Store A: Recife, more expensive
    for key, cnpj, city, price in [
        ("3" * 44, "11111111000100", "Recife", "4.50"),
        ("4" * 44, "22222222000100", "Caruaru", "3.80"),
    ]:
        domain = DomainReceipt(
            nfce_key=key,
            store=DomainStore(name="Novo", cnpj=cnpj, city=city, state="PE"),
            issued_at=datetime(2025, 12, 16),
            total=Decimal("100.00"),
            raw_items=[RawItem("ACUCAR 5KG", Decimal("1"), "PCT", Decimal("22.50"), Decimal("22.50"))],
        )
        orm_r = save_receipt(session, domain)
        save_normalized_items(session, orm_r, [
            NormalizedItem(
                raw_description="ACUCAR 5KG",
                canonical_name="Açúcar Cristal",
                brand="União",
                category="Mercearia",
                quantity=Decimal("1"),
                unit="PCT",
                unit_price=Decimal(price) * 5,
                total_price=Decimal(price) * 5,
                base_unit="kg",
                base_quantity=Decimal("5.0"),
                price_per_base_unit=Decimal(price),
            )
        ])
    session.commit()

    stores = compare_stores(session, "Açúcar Cristal", brand="União")
    assert len(stores) == 2
    # Should be ordered cheapest first
    assert stores[0].price_per_base_unit < stores[1].price_per_base_unit
    assert stores[0].store_city == "Caruaru"


# ---------------------------------------------------------------------------
# get_basket_trend
# ---------------------------------------------------------------------------

def test_get_basket_trend_groups_by_month(session):
    _insert_two_receipts(session)
    trend = get_basket_trend(session)
    # Two different months (Nov 2025 and Jan 2026)
    assert len(trend) == 2
    months = [(t.year, t.month) for t in trend]
    assert (2025, 11) in months
    assert (2026, 1) in months


def test_get_basket_trend_sums_totals(session):
    _insert_two_receipts(session)
    trend = get_basket_trend(session)
    nov = next(t for t in trend if t.month == 11)
    assert nov.total == Decimal("199.80")
    assert nov.receipt_count == 1


def test_get_basket_trend_store_filter(session):
    _insert_two_receipts(session)
    # Both receipts share the same CNPJ default "12345678000100"
    trend = get_basket_trend(session, store_cnpj="12345678000100")
    assert len(trend) == 2

    trend_other = get_basket_trend(session, store_cnpj="99999999000100")
    assert len(trend_other) == 0


# ---------------------------------------------------------------------------
# list helpers
# ---------------------------------------------------------------------------

def test_list_receipts(session):
    save_receipt(session, _make_domain_receipt(key="1" * 44))
    save_receipt(session, _make_domain_receipt(key="2" * 44, issued_at=datetime(2026, 1, 1)))
    session.commit()
    receipts = list_receipts(session)
    assert len(receipts) == 2
    # Most recent first
    assert receipts[0].issued_at > receipts[1].issued_at
