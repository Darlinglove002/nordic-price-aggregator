"""Price, currency and product-name normalisation.

Shops write the same thing differently ("1 299,00 kr", "NOK 1,299.00", "€1.299,00",
"WH-1000XM5" vs "WH1000XM5"). Everything downstream works on the normalised forms.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# Units of currency per 1 EUR. Used only when the live ECB rates cannot be fetched.
FALLBACK_RATES_PER_EUR: dict[str, Decimal] = {
    "EUR": Decimal("1"),
    "SEK": Decimal("11.40"),
    "NOK": Decimal("11.70"),
    "DKK": Decimal("7.46"),
    "GBP": Decimal("0.85"),
    "USD": Decimal("1.08"),
}

_CENT = Decimal("0.01")


class PriceParseError(ValueError):
    pass


def parse_price(text: str) -> Decimal:
    """Parse a human-formatted price into a Decimal, ignoring currency marks.

    Handles space/NBSP thousands, and both ``1,299.00`` and ``1.299,00`` styles.
    A single separator followed by exactly three digits is treated as a thousands
    separator (``1.299`` -> 1299); anything else as a decimal point (``12,90`` -> 12.90).
    """
    cleaned = re.sub(r"[^\d.,]", "", text.replace(" ", " ").replace(" ", " "))
    if not re.search(r"\d", cleaned):
        raise PriceParseError(f"no digits in price text: {text!r}")
    cleaned = cleaned.strip(".,")

    has_dot, has_comma = "." in cleaned, "," in cleaned
    decimal_sep: str | None = None
    if has_dot and has_comma:
        decimal_sep = "." if cleaned.rfind(".") > cleaned.rfind(",") else ","
    elif has_dot or has_comma:
        sep = "." if has_dot else ","
        tail = cleaned.rpartition(sep)[2]
        if cleaned.count(sep) == 1 and len(tail) != 3:
            decimal_sep = sep

    if decimal_sep:
        int_part, _, frac = cleaned.rpartition(decimal_sep)
        cleaned = f"{re.sub(r'[.,]', '', int_part) or '0'}.{frac}"
    else:
        cleaned = re.sub(r"[.,]", "", cleaned)
    try:
        return Decimal(cleaned).quantize(_CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise PriceParseError(f"cannot parse price: {text!r}") from exc


def to_eur(amount: Decimal, currency: str, rates_per_eur: Mapping[str, Decimal]) -> Decimal:
    try:
        rate = rates_per_eur[currency.upper()]
    except KeyError as exc:
        raise KeyError(f"no exchange rate for currency {currency!r}") from exc
    return (amount / rate).quantize(_CENT, rounding=ROUND_HALF_UP)


# Marketing noise that differs between shops but does not identify the product.
_NOISE_TOKENS = frozenset(
    {"new", "original", "official", "genuine", "the", "with", "and", "for", "a", "an", "of"}
)


# Letters that Unicode does not decompose into base letter + accent.
_FOLD = str.maketrans(
    {"ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "ð": "d", "þ": "th", "ß": "ss", "ł": "l"}
)


def normalize_name(name: str) -> str:
    """Canonical key for a product name, comparable across shops.

    casefold + strip accents (å/ä/ö/é via NFKD, ø/æ/ð via a table), drop hyphens inside
    model codes, drop punctuation and marketing noise, then sort tokens so word order
    does not matter.
    """
    text = unicodedata.normalize("NFKD", name.translate(_FOLD))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    text = re.sub(r"(?<=[0-9a-z])[-_/](?=[0-9a-z])", "", text)  # WH-1000XM5 -> wh1000xm5
    tokens = re.findall(r"[0-9a-z]+", text)
    tokens = [t for t in tokens if t not in _NOISE_TOKENS]
    return " ".join(sorted(tokens))
