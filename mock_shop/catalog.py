"""Deterministic fake catalogue shared by four mock shops.

Same underlying products, but every shop has its own currency, price format, markup,
name style and a different subset of the range. Prices and stock change per "epoch"
(advance it via POST /_admin/advance) so the aggregator has something to detect.
"""

from __future__ import annotations

import html
import random
from dataclasses import dataclass

PAGE_SIZE = 20

# Units of currency per 1 EUR (mirrors app.normalize.FALLBACK_RATES_PER_EUR).
RATES_PER_EUR = {"EUR": 1.0, "SEK": 11.40, "NOK": 11.70, "DKK": 7.46}

BRANDS = ["Sony", "Bose", "Logitech", "Anker", "JBL", "Samsung", "Philips", "Xiaomi", "Razer"]
CATEGORIES = {  # name -> (model prefix, min EUR, max EUR)
    "Headphones": ("WH", 25, 380),
    "Speaker": ("SP", 20, 300),
    "Keyboard": ("KB", 15, 220),
    "Mouse": ("MX", 10, 130),
    "Webcam": ("WC", 20, 180),
    "Charger": ("PD", 8, 90),
    "Router": ("RT", 30, 260),
    "Powerbank": ("PB", 12, 110),
}
COLORS = ["Black", "White", "Silver", "Blue", "Red"]


@dataclass(frozen=True)
class Product:
    index: int
    brand: str
    category: str
    model: str
    color: str
    base_eur: float


@dataclass(frozen=True)
class Shop:
    slug: str
    name: str
    currency: str
    sku_prefix: str
    markup: float
    name_style: int
    coverage: float


SHOPS: dict[str, Shop] = {
    s.slug: s
    for s in (
        Shop("fjordtech", "FjordTech", "SEK", "FJT", 1.00, 0, 0.92),
        Shop("nordvik", "Nordvik", "NOK", "NVK", 1.03, 1, 0.88),
        Shop("danskgear", "DanskGear", "DKK", "DGR", 0.98, 2, 0.90),
        Shop("suomishop", "Suomi Shop", "EUR", "SUO", 1.05, 3, 0.85),
    )
}


def build_products(count: int, seed: int = 7) -> list[Product]:
    rng = random.Random(seed)
    products: list[Product] = []
    seen: set[tuple[str, str]] = set()
    while len(products) < count:
        brand = rng.choice(BRANDS)
        category = rng.choice(list(CATEGORIES))
        prefix, low, high = CATEGORIES[category]
        model = f"{prefix}-{rng.randint(100, 9999)}{rng.choice(['', 'X', 'XM', 'Pro'])}"
        if (brand, model) in seen:
            continue
        seen.add((brand, model))
        products.append(
            Product(
                index=len(products),
                brand=brand,
                category=category,
                model=model,
                color=rng.choice(COLORS),
                base_eur=round(rng.uniform(low, high), 2),
            )
        )
    return products


def product_name(shop: Shop, p: Product) -> str:
    return {
        0: f"{p.brand} {p.model} {p.category}, {p.color}",
        1: f"{p.brand.upper()} {p.model.replace('-', '')} {p.color.lower()} {p.category.lower()}",
        2: f"{p.category} {p.brand} {p.model} ({p.color})",
        3: f"{p.brand} {p.model} {p.color} {p.category} - NEW",
    }[shop.name_style]


def carries(shop: Shop, p: Product) -> bool:
    return random.Random(f"cov:{shop.slug}:{p.index}").random() < shop.coverage


def price_local(shop: Shop, p: Product, epoch: int) -> float:
    price = p.base_eur * shop.markup * RATES_PER_EUR[shop.currency]
    if epoch > 0:
        rng = random.Random(f"price:{shop.slug}:{p.index}:{epoch}")
        roll = rng.random()
        if roll < 0.20:
            price *= 1 - rng.uniform(0.05, 0.25)  # sale
        elif roll < 0.30:
            price *= 1 + rng.uniform(0.03, 0.12)  # increase
    return round(price) if shop.currency != "EUR" else round(price, 2)


def in_stock(shop: Shop, p: Product, epoch: int) -> bool:
    return random.Random(f"stock:{shop.slug}:{p.index}:{epoch}").random() > 0.06


def _group(n: int, sep: str) -> str:
    return f"{n:,}".replace(",", sep)


def format_price(currency: str, value: float) -> str:
    whole, cents = divmod(round(value * 100), 100)
    if currency == "SEK":
        return f"{_group(whole, chr(0xA0))},{cents:02d} kr"
    if currency == "NOK":
        return f"NOK {whole:,}.{cents:02d}"
    if currency == "DKK":
        return f"{_group(whole, '.')},{cents:02d} kr."
    return f"{_group(whole, chr(0xA0))},{cents:02d} €"


def render_page(shop: Shop, products: list[Product], page: int, epoch: int) -> str | None:
    catalogue = [p for p in products if carries(shop, p)]
    total_pages = max(1, -(-len(catalogue) // PAGE_SIZE))
    if not 1 <= page <= total_pages:
        return None
    rows = []
    for p in catalogue[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]:
        sku = f"{shop.sku_prefix}{p.index:05d}"
        available = in_stock(shop, p, epoch)
        rows.append(
            f'<li class="product" data-id="{sku}">'
            f'<a class="title" href="/shop/{shop.slug}/p/{sku}">{html.escape(product_name(shop, p))}</a>'
            f'<span class="price">{format_price(shop.currency, price_local(shop, p, epoch))}</span>'
            f'<span class="stock {"in" if available else "out"}">'
            f"{'In stock' if available else 'Out of stock'}</span></li>"
        )
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><title>{shop.name}</title></head><body>"
        f"<h1>{shop.name}</h1><ul class='products'>{''.join(rows)}</ul>"
        f"<nav class='pager'><span class='current'>Page {page} of {total_pages}</span></nav>"
        "</body></html>"
    )
