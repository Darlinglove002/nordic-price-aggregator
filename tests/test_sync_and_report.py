from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select

from app.db.models import PriceSnapshot, Product
from app.domain import ScrapedItem
from app.normalize import FALLBACK_RATES_PER_EUR
from app.scrapers.base import ScrapeResult
from app.services.report import build_report, render_report
from app.services.sync import run_sync


class FakeScraper:
    """Duck-typed stand-in for a PaginatedScraper; each call to scrape() pops the next result."""

    def __init__(self, slug, currency, results):
        self.slug, self.name, self.currency = slug, slug.title(), currency
        self._results = list(results)

    async def scrape(self):
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def item(ext_id, name, price, *, stock=True, currency="SEK"):
    return ScrapedItem(ext_id, name, Decimal(price), currency, stock, f"http://shop.test/{ext_id}")


def result(*items, pages_failed=0):
    return ScrapeResult(items=list(items), pages_total=1 + pages_failed, pages_failed=pages_failed)


async def sync(session_factory, scrapers):
    return await run_sync(session_factory, scrapers, dict(FALLBACK_RATES_PER_EUR), trigger="manual")


async def test_first_run_stores_baseline_without_changes(session_factory):
    scraper = FakeScraper("fjord", "SEK", [result(item("a", "Sony WH-1000XM5", "1000"))])
    summary = await sync(session_factory, [scraper])

    assert summary.status == "ok" and summary.items_saved == 1
    async with session_factory() as session:
        report = await build_report(session)
    assert not report.has_baseline
    assert "Baseline stored" in render_report(report)[0]


async def test_second_run_detects_drop_out_of_stock_and_back_in_stock(session_factory):
    scraper = FakeScraper(
        "fjord",
        "SEK",
        [
            result(
                item("drop", "Sony WH-1000XM5", "1000"),
                item("gone", "Bose QC45", "2000"),
                item("back", "JBL Flip 6", "900", stock=False),
                item("same", "Anker Charger", "300"),
                item("tiny", "Razer Mouse", "1000"),
            ),
            result(
                item("drop", "Sony WH-1000XM5", "800"),  # -20%
                item("gone", "Bose QC45", "2000", stock=False),
                item("back", "JBL Flip 6", "900", stock=True),
                item("same", "Anker Charger", "300"),
                item("tiny", "Razer Mouse", "995"),  # -0.5%, below the 1% threshold
            ),
        ],
    )
    await sync(session_factory, [scraper])
    await sync(session_factory, [scraper])

    async with session_factory() as session:
        report = await build_report(session, min_drop_percent=1.0)

    assert [c.product for c in report.drops] == ["Sony WH-1000XM5"]
    assert report.drops[0].old == Decimal("1000.00") and report.drops[0].new == Decimal("800.00")
    assert round(report.drops[0].percent) == 20
    assert [c.product for c in report.out_of_stock] == ["Bose QC45"]
    assert [c.product for c in report.back_in_stock] == ["JBL Flip 6"]

    text = "\n".join(render_report(report))
    assert "Price drops (1)" in text and "Out of stock (1)" in text and "Back in stock (1)" in text


async def test_history_is_append_only_and_products_are_not_duplicated(session_factory):
    scraper = FakeScraper(
        "fjord", "SEK", [result(item("a", "Sony", "100")), result(item("a", "Sony", "90"))]
    )
    await sync(session_factory, [scraper])
    await sync(session_factory, [scraper])
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Product)) == 1
        assert await session.scalar(select(func.count()).select_from(PriceSnapshot)) == 2


async def test_drop_on_out_of_stock_item_is_not_reported(session_factory):
    scraper = FakeScraper(
        "fjord",
        "SEK",
        [result(item("a", "Sony", "100")), result(item("a", "Sony", "50", stock=False))],
    )
    await sync(session_factory, [scraper])
    await sync(session_factory, [scraper])
    async with session_factory() as session:
        report = await build_report(session)
    assert report.drops == [] and len(report.out_of_stock) == 1


async def test_product_missing_from_latest_run_does_not_resurface_old_changes(session_factory):
    """A page that failed to scrape must not make an old price drop look new."""
    scraper = FakeScraper(
        "fjord",
        "SEK",
        [
            result(item("a", "Sony", "100"), item("b", "Bose", "100")),
            result(item("a", "Sony", "50"), item("b", "Bose", "100")),  # a: big drop
            result(item("b", "Bose", "100"), pages_failed=1),  # a is missing this time
        ],
    )
    for _ in range(3):
        await sync(session_factory, [scraper])
    async with session_factory() as session:
        report = await build_report(session)
    assert report.drops == []


async def test_one_failing_source_does_not_block_the_others(session_factory):
    good = FakeScraper("good", "SEK", [result(item("a", "Sony", "100"))])
    bad = FakeScraper("bad", "NOK", [RuntimeError("site is down")])
    summary = await sync(session_factory, [good, bad])

    assert summary.status == "partial" and summary.items_saved == 1
    by_slug = {s.slug: s for s in summary.sources}
    assert by_slug["good"].error is None and "site is down" in by_slug["bad"].error


async def test_oversized_scraped_strings_never_fail_the_batch(session_factory):
    """Regression: a >128-char slug broke the whole insert on Postgres (SQLite does not enforce it)."""
    scraper = FakeScraper(
        "fjord",
        "SEK",
        [
            result(
                item("ok", "Sony", "100"),
                item("x" * 300, "Bad id", "100"),  # id longer than the column: skipped
                item("long-name", "N" * 900, "100"),  # name longer than the column: clipped
            )
        ],
    )
    summary = await sync(session_factory, [scraper])
    assert summary.status == "ok" and summary.items_saved == 2
    async with session_factory() as session:
        names = list(await session.scalars(select(Product.name)))
    assert max(len(n) for n in names) == 512


async def test_all_sources_failing_marks_run_failed(session_factory):
    bad = FakeScraper("bad", "NOK", [RuntimeError("down")])
    assert (await sync(session_factory, [bad])).status == "failed"


async def test_fx_change_alone_is_not_a_price_drop(session_factory):
    """Prices are compared in the shop's own currency; EUR rates moving must not look like a sale."""
    scraper = FakeScraper(
        "fjord", "SEK", [result(item("a", "Sony", "1000")), result(item("a", "Sony", "1000"))]
    )
    await sync(session_factory, [scraper])
    stronger_sek = {**FALLBACK_RATES_PER_EUR, "SEK": Decimal("9.00")}
    await run_sync(session_factory, [scraper], stronger_sek, trigger="manual")
    async with session_factory() as session:
        report = await build_report(session)
    assert report.has_baseline and report.drops == []


def test_long_reports_are_split_below_the_telegram_limit():
    from app.services.report import Change, Report

    changes = [
        Change(
            f"Product {i} " + "x" * 80,
            "Shop",
            "http://s/p",
            "SEK",
            Decimal(100),
            Decimal(50),
            Decimal(4),
            50.0,
        )
        for i in range(200)
    ]
    report = Report(run_id=1, tracked=200, has_baseline=True, drops=changes)
    messages = render_report(report, max_items=200)
    assert len(messages) > 1 and all(len(m) <= 4096 for m in messages)


def test_scraped_names_are_html_escaped_in_reports():
    from app.services.report import Change, Report

    evil = Change(
        '<script>alert(1)</script> & "co"',
        "Sh<op>",
        "javascript:alert(1)",
        "SEK",
        Decimal(2),
        Decimal(1),
        Decimal(1),
        50.0,
    )
    text = "\n".join(render_report(Report(run_id=1, tracked=1, has_baseline=True, drops=[evil])))
    assert "<script>" not in text and "Sh<op>" not in text
    assert "javascript:" not in text  # non-http(s) URLs are never turned into links
