from __future__ import annotations

import html
import logging

from aiogram import Bot, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from app.context import AppContext
from app.db import crud
from app.services.sync import SyncSummary

log = logging.getLogger(__name__)

HELP = (
    "<b>Nordic Price Aggregator</b>\n"
    "/sync — scrape all shops now\n"
    "/report — price drops and stock changes since the previous sync\n"
    "/alerts — toggle the scheduled digest for this chat"
)


def make_bot(token: str) -> Bot:
    return Bot(
        token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )


def format_summary(summary: SyncSummary) -> str:
    lines = [
        f"Sync #{summary.run_id}: <b>{summary.status}</b> — "
        f"{summary.items_saved} items in {summary.seconds:.1f}s"
    ]
    for s in summary.sources:
        if s.error:
            lines.append(f"✕ {html.escape(s.slug)}: {html.escape(s.error)}")
        elif s.pages_failed:
            lines.append(
                f"⚠ {html.escape(s.slug)}: {s.items} items, {s.pages_failed}/{s.pages_total} pages failed"
            )
        else:
            lines.append(f"✓ {html.escape(s.slug)}: {s.items}")
    return "\n".join(lines)


def build_router(ctx: AppContext) -> Router:
    router = Router(name="main")
    allowed = set(ctx.settings.allowed_chat_ids)

    def is_allowed(message: Message) -> bool:
        return message.chat.id in allowed

    @router.message(CommandStart())
    @router.message(Command("help"))
    async def start(message: Message) -> None:
        if not is_allowed(message):
            return await deny(message)
        await message.answer(HELP)

    @router.message(Command("sync"))
    async def sync(message: Message) -> None:
        if not is_allowed(message):
            return await deny(message)
        if ctx.sync_running:
            return await message.answer("A sync is already running, try /report in a minute.")
        await message.answer("Sync started…")
        summary = await ctx.sync("manual")
        if summary is None:  # lost a race with another trigger
            return await message.answer("A sync is already running.")
        await message.answer(format_summary(summary))
        if summary.status != "failed":
            for text in await ctx.report_messages():
                await message.answer(text)

    @router.message(Command("report"))
    async def report(message: Message) -> None:
        if not is_allowed(message):
            return await deny(message)
        for text in await ctx.report_messages():
            await message.answer(text)

    @router.message(Command("alerts"))
    async def alerts(message: Message) -> None:
        if not is_allowed(message):
            return await deny(message)
        async with ctx.session_factory() as session, session.begin():
            subscribed = await crud.toggle_subscriber(session, message.chat.id)
        await message.answer(
            "Scheduled digest: <b>on</b> — you will get a summary after every sync."
            if subscribed
            else "Scheduled digest: <b>off</b>."
        )

    async def deny(message: Message) -> None:
        # Tell the user their chat id so the owner can add it to ALLOWED_CHAT_IDS.
        log.warning("denied chat %s", message.chat.id)
        await message.answer(f"Access denied. Your chat id: <code>{message.chat.id}</code>")

    return router
