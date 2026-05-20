from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional


@dataclass
class RawItem:
    """Item exactly as extracted from the NFC-e HTML — no normalization yet."""
    description: str
    quantity: Decimal
    unit: str
    unit_price: Decimal
    total_price: Decimal


@dataclass
class NormalizedItem:
    """Item after Claude-assisted normalization."""
    raw_description: str
    canonical_name: str
    brand: Optional[str]
    category: str
    quantity: Decimal
    unit: str
    unit_price: Decimal
    total_price: Decimal
    # Standardized comparison fields
    base_unit: str          # e.g. "kg", "L", "un"
    base_quantity: Decimal  # quantity expressed in base_unit
    price_per_base_unit: Decimal


@dataclass
class Store:
    name: str
    cnpj: str
    city: str
    state: str
    address: Optional[str] = None


@dataclass
class Receipt:
    nfce_key: str
    store: Store
    issued_at: datetime
    total: Decimal
    raw_items: list[RawItem] = field(default_factory=list)
    normalized_items: list[NormalizedItem] = field(default_factory=list)
    source_url: Optional[str] = None
