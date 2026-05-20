"""
Repository layer: all DB reads and writes go through here.

Key operations
--------------
save_receipt()          — persist a Receipt (with raw items); idempotent on nfce_key
save_normalized_items() — link ReceiptItems to Products after normalization
get_price_history()     — price/base_unit over time for a canonical product
compare_stores()        — latest price per store for a canonical product
get_basket_trend()      — total spend per receipt, grouped by month
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, func, and_, or_
from sqlalchemy.orm import Session

from receipt_analyzer.db.orm import Store, Receipt, Product, ReceiptItem
from receipt_analyzer.models.receipt import (
    Receipt as DomainReceipt,
    NormalizedItem,
)


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------

def get_or_create_store(session: Session, cnpj: str, name: str, city: str, state: str,
                        address: str | None = None) -> Store:
    store = session.scalar(select(Store).where(Store.cnpj == cnpj))
    if store is None:
        store = Store(cnpj=cnpj, name=name, city=city, state=state, address=address)
        session.add(store)
        session.flush()
    return store


def get_or_create_product(session: Session, canonical_name: str, brand: str | None,
                          category: str, base_unit: str) -> Product:
    stmt = select(Product).where(
        Product.canonical_name == canonical_name,
        # Handle NULL brand: both NULL matches NULL, both non-NULL must match
        or_(
            and_(Product.brand.is_(None), brand is None),
            Product.brand == brand,
        ) if brand is None else Product.brand == brand,
    )
    product = session.scalar(stmt)
    if product is None:
        product = Product(
            canonical_name=canonical_name,
            brand=brand,
            category=category,
            base_unit=base_unit,
        )
        session.add(product)
        session.flush()
    return product


def save_receipt(session: Session, domain: DomainReceipt) -> Receipt | None:
    """
    Persist a Receipt and its raw items.

    Returns the ORM Receipt, or None if the NFC-e key already exists
    (idempotent — re-scanning the same receipt is safe).
    """
    existing = session.scalar(select(Receipt).where(Receipt.nfce_key == domain.nfce_key))
    if existing is not None:
        return None  # already saved

    store = get_or_create_store(
        session,
        cnpj=domain.store.cnpj or "00000000000000",
        name=domain.store.name,
        city=domain.store.city,
        state=domain.store.state,
        address=domain.store.address,
    )

    receipt = Receipt(
        nfce_key=domain.nfce_key,
        store_id=store.id,
        issued_at=domain.issued_at,
        total=domain.total,
        source_url=domain.source_url,
    )
    session.add(receipt)
    session.flush()

    for raw in domain.raw_items:
        item = ReceiptItem(
            receipt_id=receipt.id,
            raw_description=raw.description,
            quantity=raw.quantity,
            unit=raw.unit,
            unit_price=raw.unit_price,
            total_price=raw.total_price,
        )
        session.add(item)

    return receipt


def save_normalized_items(session: Session, receipt_orm: Receipt,
                          normalized: list[NormalizedItem]) -> None:
    """
    Link each ReceiptItem on `receipt_orm` to its canonical Product and store
    the computed base-unit price.  Matches by position (same order as raw_items).
    """
    items = (
        session.scalars(
            select(ReceiptItem)
            .where(ReceiptItem.receipt_id == receipt_orm.id)
            .order_by(ReceiptItem.id)
        )
        .all()
    )

    for orm_item, norm in zip(items, normalized):
        product = get_or_create_product(
            session,
            canonical_name=norm.canonical_name,
            brand=norm.brand,
            category=norm.category,
            base_unit=norm.base_unit,
        )
        orm_item.product_id = product.id
        orm_item.base_unit = norm.base_unit
        orm_item.base_quantity = norm.base_quantity
        orm_item.price_per_base_unit = norm.price_per_base_unit


# ---------------------------------------------------------------------------
# Read / comparison queries
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PricePoint:
    date: datetime
    store_name: str
    store_city: str
    price_per_base_unit: Decimal
    base_unit: str
    receipt_id: int


@dataclass(frozen=True)
class StorePrice:
    store_name: str
    store_city: str
    store_cnpj: str
    latest_date: datetime
    price_per_base_unit: Decimal
    base_unit: str


@dataclass(frozen=True)
class MonthlySpend:
    year: int
    month: int
    total: Decimal
    receipt_count: int


def get_price_history(session: Session, canonical_name: str,
                      brand: str | None = None) -> list[PricePoint]:
    """
    Return every price observation for a canonical product, ordered by date.
    If brand is None, aggregates across all brands with that canonical name.
    """
    stmt = (
        select(
            Receipt.issued_at,
            Store.name,
            Store.city,
            ReceiptItem.price_per_base_unit,
            ReceiptItem.base_unit,
            Receipt.id,
        )
        .join(ReceiptItem, ReceiptItem.receipt_id == Receipt.id)
        .join(Product, Product.id == ReceiptItem.product_id)
        .join(Store, Store.id == Receipt.store_id)
        .where(
            Product.canonical_name == canonical_name,
            ReceiptItem.price_per_base_unit.is_not(None),
        )
    )
    if brand is not None:
        stmt = stmt.where(Product.brand == brand)

    stmt = stmt.order_by(Receipt.issued_at)
    rows = session.execute(stmt).all()

    return [
        PricePoint(
            date=row[0],
            store_name=row[1],
            store_city=row[2],
            price_per_base_unit=row[3],
            base_unit=row[4],
            receipt_id=row[5],
        )
        for row in rows
    ]


def compare_stores(session: Session, canonical_name: str,
                   brand: str | None = None) -> list[StorePrice]:
    """
    For each store, return the LATEST recorded price for the product.
    Useful for answering "who charges less for this item right now?"
    """
    # Subquery: most recent receipt per store that has this product
    latest_per_store = (
        select(
            Store.id.label("store_id"),
            func.max(Receipt.issued_at).label("latest_at"),
        )
        .join(Receipt, Receipt.store_id == Store.id)
        .join(ReceiptItem, ReceiptItem.receipt_id == Receipt.id)
        .join(Product, Product.id == ReceiptItem.product_id)
        .where(
            Product.canonical_name == canonical_name,
            ReceiptItem.price_per_base_unit.is_not(None),
        )
        .group_by(Store.id)
    )
    if brand is not None:
        latest_per_store = latest_per_store.where(Product.brand == brand)
    latest_per_store = latest_per_store.subquery()

    stmt = (
        select(
            Store.name,
            Store.city,
            Store.cnpj,
            latest_per_store.c.latest_at,
            ReceiptItem.price_per_base_unit,
            ReceiptItem.base_unit,
        )
        .join(latest_per_store, latest_per_store.c.store_id == Store.id)
        .join(Receipt, and_(
            Receipt.store_id == Store.id,
            Receipt.issued_at == latest_per_store.c.latest_at,
        ))
        .join(ReceiptItem, ReceiptItem.receipt_id == Receipt.id)
        .join(Product, Product.id == ReceiptItem.product_id)
        .where(
            Product.canonical_name == canonical_name,
            ReceiptItem.price_per_base_unit.is_not(None),
        )
        .order_by(ReceiptItem.price_per_base_unit)
    )
    if brand is not None:
        stmt = stmt.where(Product.brand == brand)

    rows = session.execute(stmt).all()
    return [
        StorePrice(
            store_name=row[0],
            store_city=row[1],
            store_cnpj=row[2],
            latest_date=row[3],
            price_per_base_unit=row[4],
            base_unit=row[5],
        )
        for row in rows
    ]


def get_basket_trend(session: Session, store_cnpj: str | None = None) -> list[MonthlySpend]:
    """
    Monthly total spend (sum of receipt totals), optionally filtered by store.
    Helps distinguish "I'm buying more" from "prices went up".
    """
    stmt = (
        select(
            func.strftime("%Y", Receipt.issued_at).label("yr"),
            func.strftime("%m", Receipt.issued_at).label("mo"),
            func.sum(Receipt.total).label("total"),
            func.count(Receipt.id).label("n"),
        )
        .join(Store, Store.id == Receipt.store_id)
        .group_by("yr", "mo")
        .order_by("yr", "mo")
    )
    if store_cnpj is not None:
        stmt = stmt.where(Store.cnpj == store_cnpj)

    rows = session.execute(stmt).all()
    return [
        MonthlySpend(year=int(row[0]), month=int(row[1]), total=row[2], receipt_count=row[3])
        for row in rows
    ]


def list_products(session: Session) -> list[Product]:
    return session.scalars(select(Product).order_by(Product.canonical_name)).all()


def list_receipts(session: Session) -> list[Receipt]:
    return session.scalars(
        select(Receipt).order_by(Receipt.issued_at.desc())
    ).all()
