"""Scraper for the bundled mock Nordic shops (see ``mock_shop/``)."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.domain import ScrapedItem
from app.normalize import parse_price
from app.scrapers.base import Page, PaginatedScraper, ParseError

# (slug, display name, currency). Kept in sync with mock_shop/catalog.py by a test.
MOCK_SHOPS: tuple[tuple[str, str, str], ...] = (
    ("fjordtech", "FjordTech", "SEK"),
    ("nordvik", "Nordvik", "NOK"),
    ("danskgear", "DanskGear", "DKK"),
    ("suomishop", "Suomi Shop", "EUR"),
)

_PAGE_OF = re.compile(r"of\s+(\d+)", re.IGNORECASE)


def parse_listing(html: str, page_url: str, currency: str) -> Page:
    soup = BeautifulSoup(html, "html.parser")

    current = soup.select_one("nav.pager .current")
    match = _PAGE_OF.search(current.get_text(" ", strip=True)) if current else None
    if not match:
        raise ParseError(f"{page_url}: pager not found, layout changed?")
    total_pages = int(match.group(1))

    items: list[ScrapedItem] = []
    for li in soup.select("ul.products li.product"):
        link = li.select_one("a.title")
        price = li.select_one(".price")
        stock = li.select_one(".stock")
        external_id = li.get("data-id")
        if not (link and price and external_id and link.get("href")):
            continue  # a malformed card must not poison the whole page
        try:
            amount = parse_price(price.get_text(" ", strip=True))
        except ValueError:
            continue
        items.append(
            ScrapedItem(
                external_id=str(external_id),
                name=link.get_text(" ", strip=True),
                price=amount,
                currency=currency,
                in_stock=bool(stock and "out" not in (stock.get("class") or [])),
                url=urljoin(page_url, str(link["href"])),
            )
        )
    return Page(items=items, total_pages=total_pages)


class MockShopScraper(PaginatedScraper):
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        base_url: str,
        slug: str,
        name: str,
        currency: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(client, **kwargs)
        self.slug, self.name, self.currency = slug, name, currency
        self._base_url = base_url.rstrip("/")

    def page_url(self, number: int) -> str:
        return f"{self._base_url}/shop/{self.slug}/?page={number}"

    def parse_page(self, html: str, page_url: str) -> Page:
        return parse_listing(html, page_url, self.currency)
