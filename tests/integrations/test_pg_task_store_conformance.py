"""The Postgres task store, held to the shared conformance suite. Needs Postgres."""

from __future__ import annotations

import os

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from substrate.integrations.storage.pg_task_store import PgTaskStore
from substrate.testing.conformance.task_store import TaskStoreConformance

pytestmark = [pytest.mark.requires_postgres]

_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb")


class TestPgTaskStore(TaskStoreConformance):
    @pytest.fixture
    async def store(self):
        engine = create_async_engine(_URL, pool_pre_ping=True)
        store = PgTaskStore(async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False))
        try:
            await store.setup()
        except Exception as exc:  # noqa: BLE001
            await engine.dispose()
            pytest.skip(f"Postgres not reachable: {exc}")
        yield store
        await engine.dispose()
