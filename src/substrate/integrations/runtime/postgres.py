"""PostgresRuntimeStore — the runtime store for workers on several machines.

All the rules live in ``SqlRuntimeStore``; this is only the connection to PostgreSQL:
an ``asyncpg`` pool, transactions, and the two things PostgreSQL needs that SQLite does
not.

* ``lock(key)`` takes a transaction-scoped advisory lock. Several workers write
  concurrently here, and a signal landing between a run deciding to sleep and sleeping
  must not slip through, nor two writers pick the same ``seq``; the lock serialises
  whatever touches one run.
* Deadlocks (two transactions each holding a run the other wants — a parent ending
  while its child does) are the database's to detect; the store retries the whole
  transaction.

Needs the ``postgres`` extra (``asyncpg``).
"""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from typing import Any

from substrate.runtime.sql_store import SqlRuntimeStore
from substrate.stores.database import Tx
from substrate.logger import setup_logging

logger = setup_logging()

_PLACEHOLDER = re.compile(r"\?")


def _numbered(sql: str) -> str:
    """``?`` placeholders -> ``$1, $2, ...``. The store's SQL has no literal ``?``."""
    count = 0

    def sub(_match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"${count}"

    return _PLACEHOLDER.sub(sub, sql)


class _PgTx:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def execute(self, sql: str, *params: Any) -> int:
        status = await self._conn.execute(_numbered(sql), *params)
        # asyncpg returns e.g. "UPDATE 3"
        try:
            return int(status.split()[-1])
        except (ValueError, IndexError):
            return 0

    async def fetchone(self, sql: str, *params: Any) -> Mapping[str, Any] | None:
        return await self._conn.fetchrow(_numbered(sql), *params)

    async def fetchall(self, sql: str, *params: Any) -> list[Mapping[str, Any]]:
        return await self._conn.fetch(_numbered(sql), *params)

    async def lock(self, key: str) -> None:
        await self._conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", key)


class PostgresDatabase:
    auto_pk = "BIGSERIAL PRIMARY KEY"

    def __init__(self, dsn: str, *, pool_min_size: int = 2, pool_max_size: int = 10) -> None:
        self._dsn = dsn
        self._min = pool_min_size
        self._max = pool_max_size
        self._pool: Any = None

    async def start(self) -> None:
        if self._pool is not None:
            return
        import asyncpg

        self._pool = await asyncpg.create_pool(self._dsn, min_size=self._min, max_size=self._max)

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def script(self, ddl: str) -> None:
        assert self._pool is not None, "database not started"
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # Several processes may start at once; only one should create the tables.
                await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('substrate.runtime.schema', 0))")
                await conn.execute(ddl)

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[Tx]:
        assert self._pool is not None, "database not started"
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                yield _PgTx(conn)

    async def reclaim(self) -> None:
        assert self._pool is not None, "database not started"
        async with self._pool.acquire() as conn:
            await conn.execute("VACUUM")

    def is_unique_violation(self, exc: BaseException) -> bool:
        import asyncpg

        return isinstance(exc, asyncpg.UniqueViolationError)

    def is_retryable(self, exc: BaseException) -> bool:
        import asyncpg

        return isinstance(exc, (asyncpg.DeadlockDetectedError, asyncpg.SerializationError))


class PostgresRuntimeStore(SqlRuntimeStore):
    """``SqlRuntimeStore`` over PostgreSQL."""

    def __init__(self, dsn: str, *, pool_min_size: int = 2, pool_max_size: int = 10, **options: Any) -> None:
        super().__init__(PostgresDatabase(dsn, pool_min_size=pool_min_size, pool_max_size=pool_max_size), **options)

    async def aclose(self) -> None:
        await self._db.aclose()


__all__ = ["PostgresDatabase", "PostgresRuntimeStore"]
