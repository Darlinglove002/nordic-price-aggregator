"""Entry point.

python -m app.main          run the scheduler (+ Telegram bot if a token is configured)
python -m app.main sync     one-off sync, print the report to stdout, exit
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re

from app.bot.handlers import build_router, format_summary, make_bot
from app.config import Settings, get_settings
from app.context import AppContext
from app.db.session import make_engine, make_session_factory
from app.scheduler import bot_notifier, build_scheduler
from app.scrapers import build_client, build_scrapers

log = logging.getLogger("app")


def _plain(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


async def _build_context(settings: Settings) -> tuple[AppContext, object]:
    engine = make_engine(settings.database_url)
    client = build_client(settings)
    ctx = AppContext(
        settings=settings,
        session_factory=make_session_factory(engine),
        client=client,
        scrapers=build_scrapers(settings, client),
    )
    return ctx, engine


async def run_once(settings: Settings) -> int:
    ctx, engine = await _build_context(settings)
    try:
        summary = await ctx.sync("manual")
        assert summary is not None
        print(_plain(format_summary(summary)))
        for text in await ctx.report_messages():
            print(_plain(text))
        return 0 if summary.status != "failed" else 1
    finally:
        await ctx.client.aclose()
        await engine.dispose()


async def run_service(settings: Settings) -> None:
    ctx, engine = await _build_context(settings)
    bot = (
        make_bot(settings.telegram_bot_token.get_secret_value())
        if settings.telegram_bot_token
        else None
    )
    scheduler = build_scheduler(ctx, bot_notifier(bot) if bot else None)
    scheduler.start()
    log.info(
        "scheduler started (%s)",
        settings.sync_cron or f"every {settings.sync_interval_minutes} min",
    )
    try:
        if bot:
            from aiogram import Dispatcher

            dispatcher = Dispatcher()
            dispatcher.include_router(build_router(ctx))
            if not settings.allowed_chat_ids:
                log.warning("ALLOWED_CHAT_IDS is empty: the bot will answer nobody (default deny)")
            await dispatcher.start_polling(bot)
        else:
            log.warning("TELEGRAM_BOT_TOKEN not set: running scheduler only")
            await asyncio.Event().wait()
    finally:
        scheduler.shutdown(wait=False)
        if bot:
            await bot.session.close()
        await ctx.client.aclose()
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="nordic-price-aggregator")
    parser.add_argument("command", nargs="?", choices=["run", "sync"], default="run")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # The bot token must never end up in logs: httpx logs full URLs at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    settings = get_settings()
    if args.command == "sync":
        raise SystemExit(asyncio.run(run_once(settings)))
    asyncio.run(run_service(settings))


if __name__ == "__main__":
    main()
