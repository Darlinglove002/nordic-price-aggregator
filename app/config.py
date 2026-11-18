from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://prices:prices@localhost:5432/prices"

    # Telegram. The bot refuses everyone who is not in allowed_chat_ids (default deny).
    telegram_bot_token: SecretStr | None = None
    allowed_chat_ids: list[int] = []

    # Scheduling: either a cron expression ("0 */6 * * *") or a plain interval.
    sync_cron: str | None = None
    sync_interval_minutes: int = 60

    # Scraping
    scrape_concurrency: int = 10
    http_timeout_seconds: float = 10.0
    http_retries: int = 3
    mock_shop_url: str | None = "http://localhost:8099"
    enable_books_source: bool = True

    # Normalisation / reporting
    fx_refresh: bool = True
    min_drop_percent: float = 1.0
    report_max_items: int = 15

    @field_validator("allowed_chat_ids", mode="before")
    @classmethod
    def _split_ids(cls, v: object) -> object:
        if isinstance(v, str):
            return [int(x) for x in v.split(",") if x.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
