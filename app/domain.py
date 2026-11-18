from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ScrapedItem:
    """One product row as seen on a shop page, before normalisation."""

    external_id: str
    name: str
    price: Decimal
    currency: str  # ISO 4217, e.g. "SEK"
    in_stock: bool
    url: str
