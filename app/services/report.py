"""Compare each product's latest snapshot with the one before it and render a digest."""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db import crud
from app.db.models import PriceSnapshot, Product, Source

TELEGRAM_LIMIT = 4000  # hard limit is 4096; keep a margin


@dataclass(frozen=True, slots=True)
class Change:
    product: str
    shop: str
    url: str
    currency: str = ""
    old: Decimal | None = None
    new: Decimal | None = None
    new_eur: Decimal | None = None
    percent: float = 0.0


@dataclass(slots=True)
class Report:
    run_id: int | None = None
    finished_at: datetime | None = None
    tracked: int = 0
    has_baseline: bool = False
    drops: list[Change] = field(default_factory=list)
    out_of_stock: list[Change] = field(default_factory=list)
    back_in_stock: list[Change] = field(default_factory=list)


def _rank_per_product():
    """1 = newest snapshot of a product, 2 = the one before it."""
    return (
        func.row_number()
        .over(
            partition_by=PriceSnapshot.product_id,
            order_by=(PriceSnapshot.scraped_at.desc(), PriceSnapshot.id.desc()),
        )
        .label("rn")
    )


async def build_report(session: AsyncSession, *, min_drop_percent: float = 1.0) -> Report:
    """Changes in the latest finished run versus each product's previous snapshot.

    Only products seen in the latest run are considered, so a page that failed to scrape
    cannot make an old change look new. Price drops are compared in the shop's own
    currency, so exchange-rate noise never shows up as a "discount".
    """
    report = Report()
    run = await crud.latest_finished_run(session)
    if run is None:
        return report
    report.run_id, report.finished_at = run.id, run.finished_at
    report.tracked = await crud.count_products(session)

    ranked = (
        select(
            PriceSnapshot.product_id.label("product_id"),
            PriceSnapshot.sync_run_id.label("run_id"),
            PriceSnapshot.price_local.label("price_local"),
            PriceSnapshot.currency.label("currency"),
            PriceSnapshot.price_eur.label("price_eur"),
            PriceSnapshot.in_stock.label("in_stock"),
            _rank_per_product(),
        )
    ).subquery("ranked")
    cur = aliased(ranked, name="cur")
    prev = aliased(ranked, name="prev")

    stmt = (
        select(
            Product.name,
            Product.url,
            Source.name.label("shop"),
            cur.c.currency.label("cur_currency"),
            cur.c.price_local.label("cur_price"),
            cur.c.price_eur.label("cur_eur"),
            cur.c.in_stock.label("cur_stock"),
            prev.c.currency.label("prev_currency"),
            prev.c.price_local.label("prev_price"),
            prev.c.in_stock.label("prev_stock"),
        )
        .select_from(cur)
        .join(prev, and_(prev.c.product_id == cur.c.product_id, prev.c.rn == 2))
        .join(Product, Product.id == cur.c.product_id)
        .join(Source, Source.id == Product.source_id)
        .where(cur.c.rn == 1, cur.c.run_id == run.id)
    )
    rows = (await session.execute(stmt)).all()
    report.has_baseline = bool(rows)

    for r in rows:
        base = {"product": r.name, "shop": r.shop, "url": r.url}
        if r.prev_stock and not r.cur_stock:
            report.out_of_stock.append(Change(**base))
        elif r.cur_stock and not r.prev_stock:
            report.back_in_stock.append(Change(**base))
        if r.cur_stock and r.prev_currency == r.cur_currency and r.prev_price > r.cur_price:
            percent = float((r.prev_price - r.cur_price) / r.prev_price * 100)
            if percent >= min_drop_percent:
                report.drops.append(
                    Change(
                        **base,
                        currency=r.cur_currency,
                        old=r.prev_price,
                        new=r.cur_price,
                        new_eur=r.cur_eur,
                        percent=percent,
                    )
                )
    report.drops.sort(key=lambda c: c.percent, reverse=True)
    report.out_of_stock.sort(key=lambda c: (c.shop, c.product))
    report.back_in_stock.sort(key=lambda c: (c.shop, c.product))
    return report


def _link(change: Change) -> str:
    name = html.escape(change.product)
    # Only http(s) links are rendered as links; the URL comes from scraped HTML, so it is untrusted.
    if change.url.startswith(("http://", "https://")):
        return f'<a href="{html.escape(change.url, quote=True)}">{name}</a>'
    return name


def _section(title: str, lines: list[str], total: int, limit: int) -> str:
    if not total:
        return ""
    body = "\n".join(lines[:limit])
    more = f"\n… and {total - limit} more" if total > limit else ""
    return f"<b>{title} ({total})</b>\n{body}{more}"


def render_report(report: Report, *, max_items: int = 15) -> list[str]:
    """Telegram-ready HTML messages, each safely below the 4096-character limit."""
    if report.run_id is None:
        return ["No data yet. Run /sync first."]
    if not report.has_baseline:
        return [
            f"Baseline stored: <b>{report.tracked}</b> products tracked. "
            "Changes will appear after the next sync."
        ]

    drops = [
        f"▼ {_link(c)} — {html.escape(c.shop)}\n"
        f"   {c.old} → <b>{c.new} {c.currency}</b> (−{c.percent:.0f}%, ≈ €{c.new_eur})"
        for c in report.drops
    ]
    oos = [f"✕ {_link(c)} — {html.escape(c.shop)}" for c in report.out_of_stock]
    back = [f"✓ {_link(c)} — {html.escape(c.shop)}" for c in report.back_in_stock]

    sections = [
        _section("Price drops", drops, len(report.drops), max_items),
        _section("Out of stock", oos, len(report.out_of_stock), max_items),
        _section("Back in stock", back, len(report.back_in_stock), max_items),
    ]
    sections = [s for s in sections if s]
    header = f"<b>Price report</b> · run #{report.run_id} · {report.tracked} products tracked"
    if not sections:
        return [f"{header}\nNo price drops or stock changes."]
    return _chunk([header, *sections])


def _chunk(blocks: list[str], limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Pack blocks into messages; a block longer than ``limit`` is split on line boundaries."""
    messages: list[str] = []
    current = ""
    pieces: list[str] = []
    for block in blocks:
        if len(block) <= limit:
            pieces.append(block)
            continue
        line_buffer = ""
        for line in block.split("\n"):
            if len(line_buffer) + len(line) + 1 > limit:
                pieces.append(line_buffer)
                line_buffer = ""
            line_buffer += ("\n" if line_buffer else "") + line[:limit]
        if line_buffer:
            pieces.append(line_buffer)
    for piece in pieces:
        if current and len(current) + len(piece) + 2 > limit:
            messages.append(current)
            current = piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        messages.append(current)
    return messages
