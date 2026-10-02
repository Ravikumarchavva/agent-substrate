"""Store — the engine's whole state, in one place you point at.

    store = await substrate.connect("./.substrate")

A folder is all it takes: the library owns what is inside it, the way Chroma's persist directory or a Lance
database is the library's to lay out. Nothing in it is meant to be opened by hand.

    .substrate/
      substrate.db   the source of truth — runs and their journal, threads, memory, tasks, the graph, vectors
      files/         file contents (written whole and synced before the row that names them commits)
      index/         indexes derived from substrate.db; deleting them loses nothing, they are rebuilt

``substrate.db`` is a relational database whose tables each part of the engine owns and migrates
(``stores.database.migrate``). One transaction can change several parts at once, so a crash never leaves a
turn half recorded.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import TracebackType
from typing import TypeVar

from substrate.stores.database import Database, Tx, migrate
from substrate.stores.sqlite import SqliteDatabase
from substrate.stores.memory_tables import MEMORY_SCHEMA, Memory, SessionState
from substrate.stores.thread_tables import SCHEMA as THREAD_SCHEMA
from substrate.stores.thread_tables import Threads
from substrate.version import __version__

T = TypeVar("T")
_ATTEMPTS = 4

DATABASE_FILE = "substrate.db"
FILES_DIR = "files"
INDEX_DIR = "index"

_LAYOUT = ["""
CREATE TABLE IF NOT EXISTS substrate_info (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""]


class Store:
    """A database and the folder that holds the files and indexes beside it.

    Use ``connect(folder)``; construct one directly only to put the database elsewhere (a PostgreSQL adapter)
    while the files and indexes stay in ``root``.
    """

    def __init__(self, database: Database, root: str | Path) -> None:
        self.database = database
        self.root = Path(root)
        self.files_dir = self.root / FILES_DIR
        self.index_dir = self.root / INDEX_DIR
        self._started = False

    @property
    def threads(self) -> Threads:
        """The conversation DAG (a ``ThreadStore``). Usable straight away: the first call opens the store.

        A fresh handle each time rather than one kept on the store, so a store that is simply dropped is freed by
        reference counting and releases its connection at once, not whenever the cycle collector runs."""
        return Threads(self)

    @property
    def memory(self) -> Memory:
        """Long-term memory (a ``MemoryStore``): records scoped to tenant, user, agent and session, with full-text search."""
        return Memory(self)

    @property
    def session_state(self) -> SessionState:
        """Small key/value state a conversation keeps across runs (a ``ShortTermMemory``)."""
        return SessionState(self)

    @classmethod
    def at(cls, location: str | Path = "./.substrate") -> Store:
        """The store in the folder ``location``, not yet opened: ``start()`` (or ``async with``) opens it."""
        root = Path(location).expanduser()
        return cls(SqliteDatabase(root / DATABASE_FILE), root)

    async def start(self) -> None:
        """Open it, creating whatever is missing. Idempotent."""
        if self._started:
            return
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        await migrate(self.database, "store", _LAYOUT)
        await migrate(self.database, "threads", THREAD_SCHEMA)
        await migrate(self.database, "memory", MEMORY_SCHEMA)
        async with self.database.transaction() as tx:
            await tx.execute(
                "INSERT INTO substrate_info (key, value) VALUES ('created_with', ?) ON CONFLICT DO NOTHING",
                __version__,
            )
        self._started = True

    async def run(self, fn: Callable[[Tx], Awaitable[T]]) -> T:
        """Run ``fn`` in one transaction — opening the store first if need be — and run the whole of it again if
        the database aborts it for a reason a retry fixes (a deadlock between two writers)."""
        await self.start()
        for attempt in range(_ATTEMPTS):
            try:
                async with self.database.transaction() as tx:
                    return await fn(tx)
            except Exception as exc:  # noqa: BLE001
                if attempt + 1 < _ATTEMPTS and self.database.is_retryable(exc):
                    await asyncio.sleep(0.01 * (attempt + 1))
                    continue
                raise
        raise AssertionError("unreachable")

    async def aclose(self) -> None:
        if self._started:
            self._started = False
            await self.database.aclose()

    async def __aenter__(self) -> Store:
        await self.start()
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None) -> None:
        await self.aclose()


async def connect(location: str | Path = "./.substrate") -> Store:
    """Open — creating it if there is none — the store in the folder ``location``."""
    store = Store.at(location)
    await store.start()
    return store


__all__ = ["Store", "connect"]
