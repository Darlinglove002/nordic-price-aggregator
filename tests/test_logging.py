from __future__ import annotations

import logging
from decimal import Decimal

import httpx
from sqlalchemy.ext.asyncio import create_async_engine

from app import main
from app.config import Settings
from app.context import AppContext
from app.db.models import Base
from app.db.session import make_session_factory
from app.domain import ScrapedItem
from app.logging_config import FORMAT, configure_logging
from app.normalize import FALLBACK_RATES_PER_EUR
from app.scrapers.base import ScrapeResult
from app.services.sync import run_sync


class FakeScraper:
    def __init__(self, slug, outcome):
        self.slug, self.name, self.currency = slug, slug.title(), "SEK"
        self._outcome = outcome

    async def scrape(self):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def one_item_result(*, pages_total=1, pages_failed=0):
    item = ScrapedItem("a", "Sony WH-1000XM5", Decimal("1000"), "SEK", True, "http://shop.test/a")
    return ScrapeResult(items=[item], pages_total=pages_total, pages_failed=pages_failed)


def test_log_level_comes_from_the_environment(monkeypatch):
    root = logging.getLogger()
    before = root.level
    try:
        monkeypatch.setenv("LOG_LEVEL", "debug")
        configure_logging()
        assert root.level == logging.DEBUG
        monkeypatch.setenv("LOG_LEVEL", "nonsense")
        configure_logging()
        assert root.level == logging.INFO  # a typo must not stop the service from starting
    finally:
        root.setLevel(before)


def test_configure_logging_installs_one_handler_and_is_idempotent():
    root = logging.getLogger()
    saved, level = root.handlers[:], root.level
    root.handlers = []
    try:
        configure_logging("INFO")
        configure_logging("INFO")
        assert len(root.handlers) == 1
        formatter = root.handlers[0].formatter
        assert formatter is not None and formatter._fmt == FORMAT
    finally:
        root.handlers = saved
        root.setLevel(level)


def test_httpx_stays_quiet_even_at_debug_level(monkeypatch):
    """httpx logs full request URLs at INFO; the bot token must never end up in a log."""
    root, httpx_logger = logging.getLogger(), logging.getLogger("httpx")
    before = root.level, httpx_logger.level
    try:
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        configure_logging()
        assert not httpx_logger.isEnabledFor(logging.INFO)
    finally:
        root.setLevel(before[0])
        httpx_logger.setLevel(before[1])


async def test_sync_logs_every_source_and_one_failing_source_does_not_stop_the_rest(
    session_factory, caplog
):
    good = FakeScraper("fjord", one_item_result(pages_total=3, pages_failed=1))
    broken = FakeScraper("broken", RuntimeError("boom"))
    with caplog.at_level(logging.INFO, logger="app.services.sync"):
        summary = await run_sync(
            session_factory, [good, broken], dict(FALLBACK_RATES_PER_EUR), trigger="manual"
        )

    assert summary.status == "partial"
    messages = [r.getMessage() for r in caplog.records]
    assert "sync #1 started (manual), 2 sources" in messages
    assert "fjord: stored 1 items (3 pages, 1 failed)" in messages
    assert any(
        r.levelname == "ERROR" and r.getMessage().startswith("broken: scrape failed")
        for r in caplog.records
    )
    assert messages[-1].startswith("sync #1 partial: 1 items")


async def test_one_off_sync_prints_the_report_to_stdout_and_keeps_it_out_of_the_log(
    tmp_path, monkeypatch, capsys, caplog
):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cli.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    settings = Settings(_env_file=None, fx_refresh=False)
    ctx = AppContext(
        settings=settings,
        session_factory=make_session_factory(engine),
        client=httpx.AsyncClient(),
        scrapers=[FakeScraper("fjord", one_item_result())],
    )

    async def fake_build_context(_settings):
        return ctx, engine

    monkeypatch.setattr(main, "_build_context", fake_build_context)
    with caplog.at_level(logging.INFO):
        exit_code = await main.run_once(settings)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Sync #1: ok" in out and "Baseline stored" in out
    assert "Baseline stored" not in caplog.text, "the report is output, not a log record"
    assert "sync #1 ok" in caplog.text, "the diagnostics are still logged"
