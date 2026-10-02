"""The PostgreSQL runtime store, held to the same conformance suite as SQLite.

Same assertions, same engine code (``SqlRuntimeStore``): what differs is only the
database underneath, which is exactly what the suite is there to prove.

Requires a running Postgres (``make infra-up``); skipped when unreachable.
"""

from __future__ import annotations

import os

import pytest

from substrate.integrations.runtime import PostgresRuntimeStore
from substrate.testing.conformance.runtime_store import NOW, RuntimeStoreConformance

pytestmark = [pytest.mark.requires_postgres]

_PG_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/agentdb").replace("+asyncpg", "")
_TABLES = tuple(f"rt_{t}" for t in ("meta", "runs", "run_wake", "events", "inbox", "inbox_processed", "dead_letters", "signals", "signal_claims", "spawns", "edges"))


class TestPostgresRuntimeStore(RuntimeStoreConformance):
    @pytest.fixture
    async def store(self):
        import asyncpg

        conn = await asyncpg.connect(_PG_URL)
        try:
            await conn.execute("DROP TABLE IF EXISTS " + ", ".join(_TABLES) + " CASCADE")
        finally:
            await conn.close()
        store = PostgresRuntimeStore(_PG_URL, pool_min_size=1, pool_max_size=4, clock=lambda: NOW)
        await store.start()
        yield store
        await store.aclose()
