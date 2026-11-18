from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PriceSnapshot, Product, Source, Subscriber, SyncRun, utcnow
from app.domain import ScrapedItem


async def get_or_create_source(
    session: AsyncSession, *, slug: str, name: str, currency: str
) -> Source:
    source = await session.scalar(select(Source).where(Source.slug == slug))
    if source is None:
        source = Source(slug=slug, name=name, currency=currency)
        session.add(source)
        await session.flush()
    elif (source.name, source.currency) != (name, currency):
        source.name, source.currency = name, currency
    return source


@dataclass(frozen=True, slots=True)
class PricedItem:
    item: ScrapedItem
    normalized_name: str
    price_eur: Decimal


async def save_items(
    session: AsyncSession,
    *,
    source: Source,
    run_id: int,
    items: Iterable[PricedItem],
    scraped_at: datetime | None = None,
) -> int:
    """Upsert products of one source and append one price snapshot per item."""
    scraped_at = scraped_at or utcnow()
    items = list(items)
    if not items:
        return 0

    existing = {
        p.external_id: p
        for p in await session.scalars(select(Product).where(Product.source_id == source.id))
    }
    new_products: list[Product] = []
    for priced in items:
        item = priced.item
        product = existing.get(item.external_id)
        if product is None:
            product = Product(
                source_id=source.id,
                external_id=item.external_id,
                name=item.name,
                normalized_name=priced.normalized_name,
                url=item.url,
                first_seen_at=scraped_at,
            )
            existing[item.external_id] = product
            new_products.append(product)
        elif (product.name, product.url) != (item.name, item.url):
            product.name, product.url = item.name, item.url
            product.normalized_name = priced.normalized_name
    session.add_all(new_products)
    await session.flush()  # assigns ids to the new products

    await session.execute(
        insert(PriceSnapshot),
        [
            {
                "product_id": existing[p.item.external_id].id,
                "sync_run_id": run_id,
                "scraped_at": scraped_at,
                "price_local": p.item.price,
                "currency": p.item.currency,
                "price_eur": p.price_eur,
                "in_stock": p.item.in_stock,
            }
            for p in items
        ],
    )
    return len(items)


async def start_run(session: AsyncSession, trigger: str) -> SyncRun:
    run = SyncRun(trigger=trigger, status="running")
    session.add(run)
    await session.flush()
    return run


async def latest_finished_run(session: AsyncSession) -> SyncRun | None:
    """Most recent run that actually stored data."""
    return await session.scalar(
        select(SyncRun)
        .where(SyncRun.status.in_(("ok", "partial")), SyncRun.items_saved > 0)
        .order_by(SyncRun.id.desc())
        .limit(1)
    )


async def count_products(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(Product)) or 0


# --- Telegram subscribers -------------------------------------------------------------------


async def toggle_subscriber(session: AsyncSession, chat_id: int) -> bool:
    """Flip the subscription. Returns True if the chat is subscribed afterwards."""
    if await session.get(Subscriber, chat_id) is not None:
        await session.execute(delete(Subscriber).where(Subscriber.chat_id == chat_id))
        return False
    session.add(Subscriber(chat_id=chat_id))
    return True


async def is_subscribed(session: AsyncSession, chat_id: int) -> bool:
    return await session.get(Subscriber, chat_id) is not None


async def list_subscribers(session: AsyncSession) -> list[int]:
    return list(await session.scalars(select(Subscriber.chat_id)))
