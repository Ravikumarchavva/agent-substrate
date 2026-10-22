"""Databases for tests: an in-memory SQLite one, and a throw-away PostgreSQL schema (dropped afterwards).

PostgreSQL runs only when `LOAMZ_TEST_POSTGRES_URL` points at a reachable server (CI provides one); otherwise those
tests skip, so the fast tier needs no infrastructure.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from loamz.engine.sql import Database, SqliteDatabase, postgres, sqlite

POSTGRES_URL = os.environ.get(
    "LOAMZ_TEST_POSTGRES_URL", "postgresql://postgres:postgres@localhost:5432/postgres"
)

_available: bool | None = None


async def postgres_available() -> bool:
    """Whether the test PostgreSQL answers (checked once per process)."""
    global _available
    if _available is None:
        try:
            import asyncpg

            conn = await asyncpg.connect(POSTGRES_URL, timeout=2)
            await conn.close()
            _available = True
        except Exception:  # noqa: BLE001 - any failure means "not here"
            _available = False
    return _available


@asynccontextmanager
async def sqlite_database() -> AsyncGenerator[SqliteDatabase]:
    db = sqlite(":memory:")
    await db.start()
    try:
        yield db
    finally:
        await db.aclose()


@asynccontextmanager
async def postgres_schema() -> AsyncGenerator[Database]:
    """A PostgreSQL database confined to a fresh schema, dropped on exit."""
    import asyncpg

    schema = f"t_{uuid.uuid4().hex[:12]}"
    db = postgres(POSTGRES_URL, schema=schema, pool_min_size=1, pool_max_size=4)
    await db.start()
    try:
        yield db
    finally:
        await db.aclose()
        conn = await asyncpg.connect(POSTGRES_URL)
        try:
            await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            await conn.close()
