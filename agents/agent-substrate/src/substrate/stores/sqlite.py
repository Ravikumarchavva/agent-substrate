"""SqliteDatabase — the embedded relational component: one SQLite file, stdlib only.

Durable across restarts and crashes (WAL, ``synchronous=FULL``: a transaction that returned is on disk), and
safe for any number of workers *on one host* sharing the file (SQLite's file locking works across processes),
not across machines.

SQLite allows one writer at a time, so every transaction is serialised through one connection, one lock and
one ``BEGIN IMMEDIATE`` — which is also why ``lock`` has nothing to do. Statements run on a worker thread so a
slow disk never blocks the event loop.

Every statement runs on one dedicated thread. A task cancelled while awaiting a statement does not stop the
statement — the thread finishes it — so closing the connection has to queue behind that thread's work rather
than race it (closing a SQLite connection another thread is using is a crash, not an exception).
"""

from __future__ import annotations

import asyncio
import queue
import sqlite3
import threading
import weakref
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable, TypeVar

from substrate.stores.database import Tx

T = TypeVar("T")


Job = tuple[
    Callable[[], Any], "asyncio.AbstractEventLoop | None", "asyncio.Future[Any] | None"
]


def _settle(
    future: "asyncio.Future[Any]", result: Any, error: BaseException | None
) -> None:
    if future.cancelled():
        return
    if error is not None:
        future.set_exception(error)
    else:
        future.set_result(result)


def _serve(jobs: "queue.SimpleQueue[Job | None]") -> None:
    while (job := jobs.get()) is not None:
        fn, loop, future = job
        result, error = None, None
        try:
            result = fn()
        except BaseException as exc:  # noqa: BLE001 — handed to the awaiting task
            error = exc
        if loop is None or future is None:  # a close nobody awaits
            continue
        try:
            loop.call_soon_threadsafe(_settle, future, result, error)
        except RuntimeError:  # the awaiting loop is closed: nobody is waiting for this
            pass


def _release(conn: sqlite3.Connection, jobs: "queue.SimpleQueue[Job | None]") -> None:
    """Close a connection nobody closed — a store that was simply dropped — on its own thread, then stop it.

    Runs from the garbage collector, which can fire anywhere (even inside another thread pool's ``submit``, holding
    its global lock), so it may only ``put`` on a ``SimpleQueue`` — never submit to an executor or take a lock.
    """
    jobs.put((conn.close, None, None))
    jobs.put(None)


class _Connection:
    """A SQLite connection and the one thread allowed to touch it."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._jobs: queue.SimpleQueue[Job | None] = queue.SimpleQueue()
        self._thread = threading.Thread(
            target=_serve, args=(self._jobs,), name="sqlite-store", daemon=True
        )
        self._thread.start()
        self._finalizer = weakref.finalize(self, _release, conn, self._jobs)

    def call(self, fn: Callable[[], T]) -> asyncio.Future[T]:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[T] = loop.create_future()
        self._jobs.put((fn, loop, future))
        return future

    async def close(self) -> None:
        """Close after everything already queued has finished."""
        self._finalizer.detach()
        await self.call(self.conn.close)
        self._jobs.put(None)
        self._thread.join()


class _SqliteTx:
    def __init__(self, db: _Connection) -> None:
        self._db = db

    async def execute(self, sql: str, *params: Any) -> int:
        return await self._db.call(lambda: self._db.conn.execute(sql, params).rowcount)

    async def fetchone(self, sql: str, *params: Any) -> Mapping[str, Any] | None:
        return await self._db.call(
            lambda: self._db.conn.execute(sql, params).fetchone()
        )

    async def fetchall(self, sql: str, *params: Any) -> list[Mapping[str, Any]]:
        return await self._db.call(
            lambda: self._db.conn.execute(sql, params).fetchall()
        )

    async def lock(self, key: str) -> None:
        """One writer at a time already serialises every transaction."""


class SqliteDatabase:
    dialect = "sqlite"
    auto_pk = "INTEGER PRIMARY KEY AUTOINCREMENT"

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._conn: _Connection | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._conn is not None:
            return
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        # autocommit mode: transactions are explicit, so ``BEGIN IMMEDIATE`` means what it says.
        conn = sqlite3.connect(
            self._path, check_same_thread=False, timeout=30, isolation_level=None
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        # A deleted row's bytes are overwritten with zeros rather than left in a free page: erasure has to mean it.
        conn.execute("PRAGMA secure_delete=ON")
        # FULL: the engine records an intent before it acts and the answer after, and both must outlive a
        # power cut — NORMAL could lose the last commits of a WAL.
        conn.execute("PRAGMA synchronous=FULL")
        self._conn = _Connection(conn)

    async def aclose(self) -> None:
        if self._conn is not None:
            conn, self._conn = self._conn, None
            await conn.close()

    async def script(self, ddl: str) -> None:
        assert self._conn is not None, "database not started"
        db = self._conn
        await db.call(lambda: db.conn.executescript(ddl))

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[Tx]:
        assert self._conn is not None, "database not started"
        db = self._conn
        async with self._lock:
            await db.call(lambda: db.conn.execute("BEGIN IMMEDIATE"))
            try:
                yield _SqliteTx(db)
            except BaseException:
                # Shielded: a cancelled task must still leave the transaction closed.
                await asyncio.shield(db.call(lambda: db.conn.execute("ROLLBACK")))
                raise
            else:
                await asyncio.shield(db.call(lambda: db.conn.execute("COMMIT")))

    async def reclaim(self) -> None:
        """Fold the write-ahead log (which still holds the pre-delete pages) into the database and empty it."""
        assert self._conn is not None, "database not started"
        db = self._conn
        async with self._lock:  # no transaction may be open
            await db.call(
                lambda: db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
            )

    def is_unique_violation(self, exc: BaseException) -> bool:
        return isinstance(exc, sqlite3.IntegrityError)

    def is_retryable(self, exc: BaseException) -> bool:
        return (
            isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()
        )


__all__ = ["SqliteDatabase"]
