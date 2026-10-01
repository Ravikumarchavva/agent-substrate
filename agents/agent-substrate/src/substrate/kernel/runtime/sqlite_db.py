"""SqliteDatabase — the SQLite adapter for ``SqlRuntimeStore``.

Stdlib only: the default store needs no server and no extra dependency. Durable
across restarts, safe for any number of workers *on one host* sharing the file
(SQLite's file locking works across processes), not across machines.

SQLite allows one writer at a time, so every transaction is serialised through one
connection, one lock and one ``BEGIN IMMEDIATE`` — which is also why ``lock`` has
nothing to do. Statements run on a worker thread so a slow disk never blocks the
event loop.

``:memory:`` goes through the same code, which is what tests use.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from substrate.kernel.runtime.sql_store import Tx


class _SqliteTx:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    async def execute(self, sql: str, *params: Any) -> int:
        def run() -> int:
            return self._conn.execute(sql, params).rowcount

        return await asyncio.to_thread(run)

    async def fetchone(self, sql: str, *params: Any) -> Mapping[str, Any] | None:
        def run() -> Any:
            return self._conn.execute(sql, params).fetchone()

        return await asyncio.to_thread(run)

    async def fetchall(self, sql: str, *params: Any) -> list[Mapping[str, Any]]:
        def run() -> Any:
            return self._conn.execute(sql, params).fetchall()

        return await asyncio.to_thread(run)

    async def lock(self, key: str) -> None:
        """One writer at a time already serialises every transaction."""


class SqliteDatabase:
    auto_pk = "INTEGER PRIMARY KEY AUTOINCREMENT"

    def __init__(self, path: str | Path = "./data/db/runtime.sqlite3") -> None:
        self._path = str(path)
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._conn is not None:
            return
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        # autocommit mode: transactions are explicit, so ``BEGIN IMMEDIATE`` means what it says.
        conn = sqlite3.connect(self._path, check_same_thread=False, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        if self._path != ":memory:":
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        self._conn = conn

    async def aclose(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    async def script(self, ddl: str) -> None:
        assert self._conn is not None, "database not started"
        await asyncio.to_thread(self._conn.executescript, ddl)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Tx]:
        assert self._conn is not None, "database not started"
        conn = self._conn
        async with self._lock:
            await asyncio.to_thread(conn.execute, "BEGIN IMMEDIATE")
            try:
                yield _SqliteTx(conn)
            except BaseException:
                # Shielded: a cancelled task must still leave the transaction closed.
                await asyncio.shield(asyncio.to_thread(conn.execute, "ROLLBACK"))
                raise
            else:
                await asyncio.shield(asyncio.to_thread(conn.execute, "COMMIT"))

    def is_unique_violation(self, exc: BaseException) -> bool:
        return isinstance(exc, sqlite3.IntegrityError)

    def is_retryable(self, exc: BaseException) -> bool:
        return isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()


__all__ = ["SqliteDatabase"]
