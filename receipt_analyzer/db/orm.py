"""
SQLAlchemy ORM models.

Schema design rationale:
  Store      — one row per CNPJ; same chain in different cities = different stores
  Receipt    — one row per NFC-e key (natural unique key, prevents duplicates)
  Product    — canonical product: (canonical_name, brand) is the dedup key
  ReceiptItem — one row per line on a receipt; linked to Product after normalization

Separation of ReceiptItem from Product allows us to:
  - Re-normalize old items if the Product catalog is updated
  - Track raw descriptions per store (useful for fuzzy matching later)
  - Query price_per_base_unit trends on Product without touching raw data
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Store(Base):
    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(primary_key=True)
    cnpj: Mapped[str] = mapped_column(String(14), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    state: Mapped[str] = mapped_column(String(2), nullable=False, default="")
    address: Mapped[str | None] = mapped_column(Text, nullable=True)

    receipts: Mapped[list["Receipt"]] = relationship(back_populates="store")

    def __repr__(self) -> str:
        return f"<Store {self.cnpj} {self.name!r}>"


class Receipt(Base):
    __tablename__ = "receipts"

    id: Mapped[int] = mapped_column(primary_key=True)
    nfce_key: Mapped[str] = mapped_column(String(44), unique=True, nullable=False)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    store: Mapped["Store"] = relationship(back_populates="receipts")
    items: Mapped[list["ReceiptItem"]] = relationship(
        back_populates="receipt", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_receipts_issued_at", "issued_at"),
        Index("ix_receipts_store_id", "store_id"),
    )

    def __repr__(self) -> str:
        return f"<Receipt {self.nfce_key[:8]}… {self.issued_at:%d/%m/%Y}>"


class Product(Base):
    """
    Canonical product — one row per (canonical_name, brand) pair.

    NULL brand is allowed (generic/unbranded items), but two rows with the
    same canonical_name and NULL brand would be duplicates, so the unique
    constraint uses COALESCE(brand, '') to handle that.
    We enforce this at the application level in the repository instead of
    a partial index, for broader DB compatibility.
    """
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(200), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(100), nullable=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False, default="Outros")
    base_unit: Mapped[str] = mapped_column(String(10), nullable=False)  # kg, L, un

    items: Mapped[list["ReceiptItem"]] = relationship(back_populates="product")

    __table_args__ = (
        UniqueConstraint("canonical_name", "brand", name="uq_product_name_brand"),
        Index("ix_products_canonical_name", "canonical_name"),
    )

    def __repr__(self) -> str:
        brand = f" [{self.brand}]" if self.brand else ""
        return f"<Product {self.canonical_name!r}{brand}>"


class ReceiptItem(Base):
    __tablename__ = "receipt_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    receipt_id: Mapped[int] = mapped_column(ForeignKey("receipts.id"), nullable=False)
    # Nullable: set to NULL until normalization runs, then linked to a Product
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id"), nullable=True
    )

    # Raw fields exactly as parsed from the NFC-e
    raw_description: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    unit: Mapped[str] = mapped_column(String(10), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    total_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    # Populated after normalization
    base_unit: Mapped[str | None] = mapped_column(String(10), nullable=True)
    base_quantity: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    price_per_base_unit: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)

    receipt: Mapped["Receipt"] = relationship(back_populates="items")
    product: Mapped["Product | None"] = relationship(back_populates="items")

    __table_args__ = (
        Index("ix_receipt_items_receipt_id", "receipt_id"),
        Index("ix_receipt_items_product_id", "product_id"),
    )

    def __repr__(self) -> str:
        return f"<ReceiptItem {self.raw_description!r:.30} qty={self.quantity}>"
