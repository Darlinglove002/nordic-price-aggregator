"""Scraper for https://books.toscrape.com, a public sandbox built for scraping practice."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.domain import ScrapedItem
from app.normalize import parse_price
from app.scrapers.base import Page, PaginatedScraper, ParseError

BASE_URL = "https://books.toscrape.com"
_PAGE_OF = re.compile(r"of\s+(\d+)", re.IGNORECASE)
_SLUG = re.compile(r"([^/]+)/index\.html$")


def parse_listing(html: str, page_url: str) -> Page:
    soup = BeautifulSoup(html, "html.parser")

    current = soup.select_one("li.current")
    match = _PAGE_OF.search(current.get_text(" ", strip=True)) if current else None
    # A single-page catalogue has no pager at all.
    total_pages = int(match.group(1)) if match else 1
    pods = soup.select("article.product_pod")
    if not pods and not match:
        raise ParseError(f"{page_url}: no product cards found, layout changed?")

    items: list[ScrapedItem] = []
    for pod in pods:
        link = pod.select_one("h3 a")
        price = pod.select_one("p.price_color")
        if not (link and price and link.get("href")):
            continue
        href = str(link["href"])
        slug = _SLUG.search(href)
        availability = pod.select_one("p.availability")
        try:
            amount = parse_price(price.get_text(strip=True))
        except ValueError:
            continue
        items.append(
            ScrapedItem(
                external_id=slug.group(1) if slug else href,
                name=str(link.get("title") or link.get_text(strip=True)),
                price=amount,
                currency="GBP",
                in_stock=bool(
                    availability and "in stock" in availability.get_text(" ", strip=True).lower()
                ),
                url=urljoin(page_url, href),
            )
        )
    return Page(items=items, total_pages=total_pages)


class BooksToScrapeScraper(PaginatedScraper):
    slug = "books-toscrape"
    name = "Books to Scrape"
    currency = "GBP"

    def page_url(self, number: int) -> str:
        return f"{BASE_URL}/catalogue/page-{number}.html"

    def parse_page(self, html: str, page_url: str) -> Page:
        return parse_listing(html, page_url)
