"""PostgresDatabase — the relational component on PostgreSQL, for workers on several machines.

All the rules live in the stores written over ``substrate.stores.Database``; this is only the connection to PostgreSQL:
an ``asyncpg`` pool, transactions, and the things PostgreSQL needs that SQLite does not.

* ``lock(key)`` takes a transaction-scoped advisory lock. Several workers write concurrently here, and a signal
  landing between a run deciding to sleep and sleeping must not slip through, nor two writers pick the same ``seq``; the
  lock serialises whatever touches one run.
* Deadlocks (two transactions each holding a run the other wants — a parent ending while its child does) are the
  database's to detect; the store retries the whole transaction.
* ``schema`` keeps everything in one PostgreSQL schema, so several stores can share a database.

``postgres_store(dsn)`` is the whole engine's state on it: the same ``Store`` as ``substrate.connect(folder)``, with
the rows in PostgreSQL and the file contents in ``files`` (a folder every worker can reach; put them in object storage by
passing an ``S3FileStore`` where a ``FileStore`` is taken).

Needs the ``postgres`` extra (``asyncpg``) and, for vectors, the ``vector`` (pgvector) extension.
"""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from substrate.stores import Store, Tx

_PLACEHOLDER = re.compile(r"\?")
_SCHEMA_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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
        await self._conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", key
        )


class PostgresDatabase:
    dialect = "postgresql"
    auto_pk = "BIGSERIAL PRIMARY KEY"

    def __init__(
        self,
        dsn: str,
        *,
        schema: str | None = None,
        pool_min_size: int = 2,
        pool_max_size: int = 10,
    ) -> None:
        if schema is not None and not _SCHEMA_NAME.match(schema):
            raise ValueError(f"not a valid schema name: {schema!r}")
        self._dsn = dsn
        self._schema = schema
        self._min = pool_min_size
        self._max = pool_max_size
        self._pool: Any = None

    async def start(self) -> None:
        if self._pool is not None:
            return
        import asyncpg

        settings: dict[str, str] = {}
        if self._schema is not None:
            conn = await asyncpg.connect(self._dsn)
            try:
                await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{self._schema}"')
            finally:
                await conn.close()
            settings["search_path"] = (
                f'"{self._schema}", public'  # public holds the pgvector extension
            )
        self._pool = await asyncpg.create_pool(
            self._dsn, min_size=self._min, max_size=self._max, server_settings=settings
        )

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def script(self, ddl: str) -> None:
        assert self._pool is not None, "database not started"
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # Several processes may start at once; only one should create the tables.
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended('substrate.schema', 0))"
                )
                await conn.execute(ddl)

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[Tx]:
        assert self._pool is not None, "database not started"
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                yield _PgTx(conn)

    async def reclaim(self) -> None:
        """``VACUUM``: dead rows' space is cleaned out of the tables and indexes. Write-ahead log segments, replicas and
        backups are the operator's to expire."""
        assert self._pool is not None, "database not started"
        async with self._pool.acquire() as conn:
            await conn.execute("VACUUM")

    def is_unique_violation(self, exc: BaseException) -> bool:
        import asyncpg

        return isinstance(exc, asyncpg.UniqueViolationError)

    def is_retryable(self, exc: BaseException) -> bool:
        import asyncpg

        return isinstance(
            exc, (asyncpg.DeadlockDetectedError, asyncpg.SerializationError)
        )


def postgres_store(
    dsn: str,
    *,
    files: str | Path = "./.substrate",
    schema: str | None = None,
    file_quota_bytes: int | None = None,
    pool_min_size: int = 2,
    pool_max_size: int = 10,
) -> Store:
    """The engine's state in PostgreSQL, not yet opened (``start()`` or ``async with`` opens it)."""
    database = PostgresDatabase(
        dsn, schema=schema, pool_min_size=pool_min_size, pool_max_size=pool_max_size
    )
    return Store(database, files, file_quota_bytes=file_quota_bytes)


__all__ = ["PostgresDatabase", "postgres_store"]
