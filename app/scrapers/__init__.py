from __future__ import annotations

import httpx

from app.config import Settings
from app.scrapers.base import FetchError, PaginatedScraper, ParseError, ScrapeResult
from app.scrapers.books import BooksToScrapeScraper
from app.scrapers.mockshop import MOCK_SHOPS, MockShopScraper

__all__ = [
    "FetchError",
    "PaginatedScraper",
    "ParseError",
    "ScrapeResult",
    "build_client",
    "build_scrapers",
]


def build_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.http_timeout_seconds, connect=5.0),
        limits=httpx.Limits(max_connections=settings.scrape_concurrency * 5),
        headers={"User-Agent": "nordic-price-aggregator/0.1 (portfolio project)"},
        follow_redirects=True,
    )


def build_scrapers(settings: Settings, client: httpx.AsyncClient) -> list[PaginatedScraper]:
    opts = {"concurrency": settings.scrape_concurrency, "retries": settings.http_retries}
    scrapers: list[PaginatedScraper] = []
    if settings.mock_shop_url:
        for slug, name, currency in MOCK_SHOPS:
            scrapers.append(
                MockShopScraper(
                    client,
                    base_url=settings.mock_shop_url,
                    slug=slug,
                    name=name,
                    currency=currency,
                    **opts,
                )
            )
    if settings.enable_books_source:
        scrapers.append(BooksToScrapeScraper(client, **opts))
    return scrapers
