from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.scrapers import PaginatedScraper
from app.services import report as report_service
from app.services import sync as sync_service


@dataclass(slots=True)
class AppContext:
    """Everything the bot, the scheduler and the CLI share."""

    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    client: httpx.AsyncClient
    scrapers: list[PaginatedScraper]
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def sync_running(self) -> bool:
        return self._lock.locked()

    async def sync(self, trigger: str) -> sync_service.SyncSummary | None:
        """Run a full sync. Returns None if one is already in progress (never two at once)."""
        if self._lock.locked():
            return None
        async with self._lock:
            rates = await sync_service.fetch_rates(self.client, refresh=self.settings.fx_refresh)
            return await sync_service.run_sync(
                self.session_factory, self.scrapers, rates, trigger=trigger
            )

    async def report_messages(self) -> list[str]:
        async with self.session_factory() as session:
            report = await report_service.build_report(
                session, min_drop_percent=self.settings.min_drop_percent
            )
        return report_service.render_report(report, max_items=self.settings.report_max_items)
