"""The Postgres memory store, held to the shared conformance suite.

Needs a reachable Postgres (``make infra-up``); skips otherwise. Uses the ``memory_records``
table, emptied before each test.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from substrate.integrations.memory import DurableMemoryStore
from substrate.kernel.testing.conformance.memory_store import MemoryStoreConformance

pytestmark = [pytest.mark.requires_postgres]

_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb")


class TestDurableMemoryStore(MemoryStoreConformance):
    @pytest.fixture
    async def store(self):
        store = DurableMemoryStore(_URL)
        try:
            await store.connect()
            await store.create_tables()
            async with store._eng().begin() as conn:
                await conn.execute(text("TRUNCATE memory_records"))
        except Exception as exc:  # noqa: BLE001 - no database to test against
            pytest.skip(f"Postgres not reachable: {exc}")
        self._store = store
        yield store
        await store.disconnect()

    async def residue(self, store, needle):
        async with store._eng().begin() as conn:
            rows = (await conn.execute(text("SELECT id FROM memory_records WHERE record::text LIKE :p OR content LIKE :p"), {"p": f"%{needle}%"})).all()
        return [r.id for r in rows]
