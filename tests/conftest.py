from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.models import Base
from app.db.session import make_session_factory


@pytest.fixture
async def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield make_session_factory(engine)
    await engine.dispose()
