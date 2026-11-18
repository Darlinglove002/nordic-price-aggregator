"""The bot is driven with fake Telegram updates and a session that records outgoing calls."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.types import Chat, Message, Update, User

from app.bot.handlers import build_router
from app.config import Settings
from app.context import AppContext
from app.db import crud

ALLOWED, STRANGER = 111, 999


class RecordingSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.texts: list[tuple[int, str]] = []

    async def make_request(self, bot, method, timeout=None):
        self.texts.append((method.chat_id, method.text))

    async def stream_content(self, *args, **kwargs):  # pragma: no cover - unused
        yield b""

    async def close(self) -> None:  # pragma: no cover - nothing to close
        return None


def command(chat_id: int, text: str, update_id: int = 1) -> Update:
    return Update(
        update_id=update_id,
        message=Message(
            message_id=update_id,
            date=datetime.now(UTC),
            chat=Chat(id=chat_id, type="private"),
            from_user=User(id=chat_id, is_bot=False, first_name="T"),
            text=text,
            entities=[{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}],
        ),
    )


@pytest.fixture
async def harness(session_factory):
    settings = Settings(allowed_chat_ids=[ALLOWED], telegram_bot_token=None)
    async with httpx.AsyncClient() as client:
        ctx = AppContext(settings, session_factory, client, scrapers=[])
        session = RecordingSession()
        bot = Bot("123456:TEST", session=session)
        dispatcher = Dispatcher()
        dispatcher.include_router(build_router(ctx))
        yield ctx, bot, dispatcher, session


@pytest.mark.parametrize("text", ["/start", "/report", "/sync", "/alerts"])
async def test_unknown_chat_is_denied_and_gets_nothing(harness, text):
    ctx, bot, dispatcher, session = harness
    await dispatcher.feed_update(bot, command(STRANGER, text))

    assert len(session.texts) == 1
    assert "Access denied" in session.texts[0][1] and str(STRANGER) in session.texts[0][1]
    async with ctx.session_factory() as db:
        assert await crud.list_subscribers(db) == []  # /alerts must not subscribe a stranger
    assert not ctx.sync_running


async def test_allowed_chat_can_toggle_alerts(harness):
    ctx, bot, dispatcher, session = harness

    await dispatcher.feed_update(bot, command(ALLOWED, "/alerts", 1))
    async with ctx.session_factory() as db:
        assert await crud.list_subscribers(db) == [ALLOWED]
    assert "on" in session.texts[-1][1]

    await dispatcher.feed_update(bot, command(ALLOWED, "/alerts", 2))
    async with ctx.session_factory() as db:
        assert await crud.list_subscribers(db) == []
    assert "off" in session.texts[-1][1]


async def test_report_before_any_sync_says_so(harness):
    _, bot, dispatcher, session = harness
    await dispatcher.feed_update(bot, command(ALLOWED, "/report"))
    assert "No data yet" in session.texts[-1][1]


async def test_empty_allowlist_denies_everyone(session_factory):
    async with httpx.AsyncClient() as client:
        ctx = AppContext(Settings(allowed_chat_ids=[]), session_factory, client, scrapers=[])
        session = RecordingSession()
        bot = Bot("123456:TEST", session=session)
        dispatcher = Dispatcher()
        dispatcher.include_router(build_router(ctx))
        await dispatcher.feed_update(bot, command(ALLOWED, "/start"))
    assert "Access denied" in session.texts[0][1]
