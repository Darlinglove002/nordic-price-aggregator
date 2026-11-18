from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.bot.handlers import format_summary
from app.context import AppContext
from app.db import crud

log = logging.getLogger(__name__)

Notify = Callable[[int, str], Awaitable[None]]


def bot_notifier(bot: Bot) -> Notify:
    async def notify(chat_id: int, text: str) -> None:
        try:
            await bot.send_message(chat_id, text)
        except TelegramAPIError:
            log.exception("could not deliver message to chat %s", chat_id)

    return notify


async def scheduled_sync(ctx: AppContext, notify: Notify | None) -> None:
    """One scheduled run: scrape, then push the digest to every subscribed (and allowed) chat."""
    summary = await ctx.sync("schedule")
    if summary is None:
        log.info("scheduled sync skipped: another sync is still running")
        return
    if notify is None:
        return

    async with ctx.session_factory() as session:
        subscribers = await crud.list_subscribers(session)
    chats = [c for c in subscribers if c in set(ctx.settings.allowed_chat_ids)]
    if not chats:
        return

    if summary.status == "failed":
        messages = [format_summary(summary)]
    else:
        messages = await ctx.report_messages()
        if summary.status == "partial":
            messages.insert(0, format_summary(summary))
    for chat_id in chats:
        for text in messages:
            await notify(chat_id, text)


def build_scheduler(ctx: AppContext, notify: Notify | None) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    if ctx.settings.sync_cron:
        trigger = CronTrigger.from_crontab(ctx.settings.sync_cron, timezone="UTC")
    else:
        trigger = IntervalTrigger(minutes=ctx.settings.sync_interval_minutes)
    scheduler.add_job(
        scheduled_sync,
        trigger,
        args=[ctx, notify],
        id="sync",
        max_instances=1,
        coalesce=True,  # a missed run is not replayed many times
        misfire_grace_time=300,
    )
    return scheduler
