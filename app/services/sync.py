from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field, replace
from decimal import Decimal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import crud
from app.db.models import SyncRun, utcnow
from app.normalize import FALLBACK_RATES_PER_EUR, normalize_name, to_eur
from app.scrapers.base import PaginatedScraper, ScrapeResult

log = logging.getLogger(__name__)

ECB_RATES_URL = "https://api.frankfurter.dev/v1/latest"

# Column sizes in app/db/models.py
MAX_EXTERNAL_ID, MAX_NAME, MAX_URL = 255, 512, 1024


async def fetch_rates(client: httpx.AsyncClient, *, refresh: bool) -> dict[str, Decimal]:
    """Units of currency per 1 EUR. Live ECB rates when possible, built-in fallback otherwise."""
    rates = dict(FALLBACK_RATES_PER_EUR)
    if not refresh:
        return rates
    try:
        response = await client.get(
            ECB_RATES_URL, params={"base": "EUR", "symbols": ",".join(rates)}, timeout=5.0
        )
        response.raise_for_status()
        live = response.json()["rates"]
        rates.update({code: Decimal(str(value)) for code, value in live.items() if value})
    except (httpx.HTTPError, KeyError, ValueError, ArithmeticError) as exc:
        log.warning("live FX rates unavailable, using fallback table: %r", exc)
    return rates


@dataclass(slots=True)
class SourceOutcome:
    slug: str
    items: int = 0
    pages_total: int = 0
    pages_failed: int = 0
    error: str | None = None


@dataclass(slots=True)
class SyncSummary:
    run_id: int
    status: str
    seconds: float
    items_saved: int
    sources: list[SourceOutcome] = field(default_factory=list)

    @property
    def errors(self) -> int:
        return sum(1 for s in self.sources if s.error) + sum(s.pages_failed for s in self.sources)


async def _persist(
    session: AsyncSession,
    scraper: PaginatedScraper,
    result: ScrapeResult,
    run: SyncRun,
    rates: dict[str, Decimal],
) -> int:
    source = await crud.get_or_create_source(
        session, slug=scraper.slug, name=scraper.name, currency=scraper.currency
    )
    priced = []
    for item in result.items:
        # Scraped strings are untrusted input: never let one oversized value fail the whole batch.
        if len(item.external_id) > MAX_EXTERNAL_ID or len(item.url) > MAX_URL:
            log.warning("%s: item with oversized id/url skipped", scraper.slug)
            continue
        if len(item.name) > MAX_NAME:
            item = replace(item, name=item.name[:MAX_NAME])
        try:
            eur = to_eur(item.price, item.currency, rates)
        except KeyError:
            log.error("%s: no FX rate for %s, item skipped", scraper.slug, item.currency)
            continue
        priced.append(crud.PricedItem(item, normalize_name(item.name), eur))
    return await crud.save_items(session, source=source, run_id=run.id, items=priced)


async def run_sync(
    session_factory: async_sessionmaker[AsyncSession],
    scrapers: list[PaginatedScraper],
    rates: dict[str, Decimal],
    *,
    trigger: str,
) -> SyncSummary:
    """Scrape every source concurrently, then store each source's data in its own transaction.

    One broken source never blocks the others; the run is "partial" if anything went wrong
    and "failed" only if nothing could be stored at all.
    """
    started = time.perf_counter()
    async with session_factory() as session, session.begin():
        run = await crud.start_run(session, trigger)
    log.info("sync #%d started (%s), %d sources", run.id, trigger, len(scrapers))

    results = await asyncio.gather(*(s.scrape() for s in scrapers), return_exceptions=True)

    outcomes: list[SourceOutcome] = []
    saved_total = 0
    for scraper, result in zip(scrapers, results, strict=True):
        outcome = SourceOutcome(slug=scraper.slug)
        outcomes.append(outcome)
        if isinstance(result, BaseException):
            if isinstance(result, asyncio.CancelledError):
                raise result
            outcome.error = f"{type(result).__name__}: {result}"[:300]
            log.error("%s: scrape failed: %s", scraper.slug, outcome.error)
            continue
        outcome.pages_total, outcome.pages_failed = result.pages_total, result.pages_failed
        try:
            async with session_factory() as session, session.begin():
                outcome.items = await _persist(session, scraper, result, run, rates)
        except Exception as exc:  # noqa: BLE001 - one source's DB error must not stop the rest
            outcome.error = f"storage error: {type(exc).__name__}"
            log.exception("%s: could not store results", scraper.slug)
            continue
        log.info(
            "%s: stored %d items (%d pages, %d failed)",
            scraper.slug,
            outcome.items,
            outcome.pages_total,
            outcome.pages_failed,
        )
        saved_total += outcome.items

    problems = sum(1 for o in outcomes if o.error) + sum(o.pages_failed for o in outcomes)
    if saved_total == 0:
        status = "failed"
    else:
        status = "partial" if problems else "ok"

    async with session_factory() as session, session.begin():
        db_run = await session.get(SyncRun, run.id)
        if db_run is None:  # the row was inserted at the start of this very run
            raise RuntimeError(f"sync run {run.id} disappeared before it was finalised")
        db_run.finished_at = utcnow()
        db_run.status = status
        db_run.items_saved = saved_total
        db_run.errors = problems

    summary = SyncSummary(
        run_id=run.id,
        status=status,
        seconds=time.perf_counter() - started,
        items_saved=saved_total,
        sources=outcomes,
    )
    log.info("sync #%d %s: %d items in %.1fs", run.id, status, saved_total, summary.seconds)
    return summary
