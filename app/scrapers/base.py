from __future__ import annotations

import asyncio
import logging
import random
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx

from app.domain import ScrapedItem

log = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class FetchError(Exception):
    """A page could not be fetched even after retries."""


class ParseError(Exception):
    """The HTML did not look like the page this parser was written for."""


async def fetch_text(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, str | int] | None = None,
    retries: int = 3,
    backoff: float = 0.5,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """GET ``url`` with exponential backoff + jitter on transport errors, timeouts and 5xx/429.

    ``retries`` is the number of *additional* attempts after the first one. Client errors
    such as 404 are not retried. Per-request timeouts come from the client's configuration.
    """
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = await client.get(url, params=params)
        except httpx.TransportError as exc:  # includes all timeouts and connection errors
            last_error = exc
        else:
            if response.status_code not in RETRYABLE_STATUS:
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise FetchError(f"{url}: HTTP {response.status_code}") from exc
                return response.text
            last_error = FetchError(f"{url}: HTTP {response.status_code}")
        if attempt < retries:
            delay = backoff * (2**attempt) * (0.75 + random.random() / 2)
            log.debug("retrying %s in %.2fs (attempt %d): %s", url, delay, attempt + 1, last_error)
            await sleep(delay)
    raise FetchError(f"{url}: giving up after {retries + 1} attempts: {last_error!r}")


@dataclass(slots=True)
class Page:
    items: list[ScrapedItem]
    total_pages: int


@dataclass(slots=True)
class ScrapeResult:
    items: list[ScrapedItem]
    pages_total: int
    pages_failed: int = 0
    errors: list[str] = field(default_factory=list)


class PaginatedScraper(ABC):
    """Scrapes a paginated catalogue: page 1 tells us the page count, the rest run concurrently."""

    slug: str
    name: str
    currency: str

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        concurrency: int = 10,
        retries: int = 3,
        backoff: float = 0.5,
    ) -> None:
        self._client = client
        self._semaphore = asyncio.Semaphore(concurrency)
        self._retries = retries
        self._backoff = backoff

    @abstractmethod
    def page_url(self, number: int) -> str: ...

    @abstractmethod
    def parse_page(self, html: str, page_url: str) -> Page: ...

    async def _load(self, number: int) -> Page:
        url = self.page_url(number)
        async with self._semaphore:
            html = await fetch_text(self._client, url, retries=self._retries, backoff=self._backoff)
        return self.parse_page(html, url)

    async def scrape(self) -> ScrapeResult:
        # Page 1 is required: without it we know neither the page count nor the layout.
        first = await self._load(1)
        items = list(first.items)
        outcomes = await asyncio.gather(
            *(self._load(n) for n in range(2, first.total_pages + 1)),
            return_exceptions=True,
        )
        errors: list[str] = []
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                if isinstance(outcome, asyncio.CancelledError):
                    raise outcome
                errors.append(str(outcome))
            else:
                items.extend(outcome.items)

        unique = {item.external_id: item for item in items}
        if errors:
            log.warning("%s: %d/%d pages failed", self.slug, len(errors), first.total_pages)
        return ScrapeResult(
            items=list(unique.values()),
            pages_total=first.total_pages,
            pages_failed=len(errors),
            errors=errors[:5],
        )
