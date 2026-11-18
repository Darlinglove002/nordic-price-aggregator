from decimal import Decimal

import pytest

from app.normalize import (
    FALLBACK_RATES_PER_EUR,
    PriceParseError,
    normalize_name,
    parse_price,
    to_eur,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1 299,00 kr", "1299.00"),  # SEK, NBSP thousands, decimal comma
        ("NOK 1,299.00", "1299.00"),  # NOK, decimal point
        ("1.299,00 kr.", "1299.00"),  # DKK, dot thousands
        ("€19,90", "19.90"),
        ("£51.77", "51.77"),
        ("£1,053.74", "1053.74"),
        ("349,5 kr", "349.50"),
        ("1.299", "1299.00"),  # single separator + 3 digits = thousands
        ("12.99", "12.99"),
        ("1 234 567,89", "1234567.89"),
        ("  99 ", "99.00"),
    ],
)
def test_parse_price(text, expected):
    assert parse_price(text) == Decimal(expected)


@pytest.mark.parametrize("text", ["", "free", "kr", "—"])
def test_parse_price_rejects_garbage(text):
    with pytest.raises(PriceParseError):
        parse_price(text)


def test_same_product_written_four_ways_has_one_key():
    names = [
        "Sony WH-1000XM5 Headphones, Black",
        "SONY WH1000XM5 black headphones",
        "Headphones Sony WH-1000XM5 (Black)",
        "Sony WH-1000XM5 Black Headphones - NEW",
    ]
    assert len({normalize_name(n) for n in names}) == 1


def test_different_variants_keep_different_keys():
    assert normalize_name("Sony WH-1000XM5 Headphones, Black") != normalize_name(
        "Sony WH-1000XM5 Headphones, Silver"
    )
    assert normalize_name("Sony WH-1000XM5 Headphones") != normalize_name(
        "Sony WH-1000XM4 Headphones"
    )


def test_accents_are_folded():
    assert normalize_name("Crème Brûlée") == "brulee creme"
    assert normalize_name("Räksmörgås Høyttaler Ærlig") == "aerlig hoyttaler raksmorgas"


def test_to_eur():
    rates = FALLBACK_RATES_PER_EUR
    assert to_eur(Decimal("1140"), "SEK", rates) == Decimal("100.00")
    assert to_eur(Decimal("10"), "EUR", rates) == Decimal("10.00")
    with pytest.raises(KeyError):
        to_eur(Decimal("1"), "XYZ", rates)
