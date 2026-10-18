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
from typing import TYPE_CHECKING, TypeVar

from substrate.stores.database import Database, Tx, migrate
from substrate.stores.sqlite import SqliteDatabase
from substrate.stores.tenant import Tenant
from substrate.types.scope import Scope
from substrate.version import __version__

if TYPE_CHECKING:
    from substrate.stores.file_tables import Files
    from substrate.stores.graph_tables import Graph
    from substrate.stores.memory_tables import Memory, SessionState
    from substrate.stores.task_tables import Tasks
    from substrate.stores.thread_tables import Threads
    from substrate.stores.vector_tables import Vectors

T = TypeVar("T")
_ATTEMPTS = 4

DATABASE_FILE = "substrate.db"
FILES_DIR = "files"
INDEX_DIR = "index"

_LAYOUT = [
    """
CREATE TABLE IF NOT EXISTS substrate_info (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""
]


class Store:
    """A database and the folder that holds the files and indexes beside it.

    Use ``connect(folder)``; construct one directly only to put the database elsewhere (a PostgreSQL adapter)
    while the files and indexes stay in ``root``.
    """

    def __init__(
        self,
        database: Database,
        root: str | Path,
        *,
        file_quota_bytes: int | None = None,
    ) -> None:
        self.database = database
        self.root = Path(root)
        self.file_quota_bytes = file_quota_bytes
        """Bytes a tenant may store in ``files`` (``None``: unlimited)."""
        self.file_quota_overrides: dict[str, int] = {}
        """Per-tenant quotas that replace the default (``Files.set_quota_override``)."""
        self.files_dir = self.root / FILES_DIR
        self.index_dir = self.root / INDEX_DIR
        self._started = False
        self._ensured: set[str] = set()

    async def ensure(self, component: str, schema: list[str]) -> None:
        """Create or upgrade the tables of a part that lives above ``stores`` (workspaces, documents) the first time it
        is used. ``schema`` is that part's ordered migrations, as for ``stores.database.migrate``. Idempotent."""
        if component in self._ensured:
            return
        await self.start()
        await migrate(self.database, component, schema)
        self._ensured.add(component)

    async def has(self, component: str) -> bool:
        """Whether ``component``'s tables exist in this database — created by this process or an earlier one.
        Asking creates nothing (a part is created the first time it is used)."""
        if component in self._ensured:
            return True
        await self.start()
        async with self.database.transaction() as tx:
            rows = await tx.fetchall(
                "SELECT version FROM substrate_migrations WHERE component = ?",
                component,
            )
        return bool(rows)

    @property
    def threads(self) -> Threads:
        """The conversation DAG (a ``ThreadStore``). Usable straight away: the first call opens the store.

        A fresh handle each time rather than one kept on the store, so a store that is simply dropped is freed by
        reference counting and releases its connection at once, not whenever the cycle collector runs."""
        from substrate.stores.thread_tables import Threads

        return Threads(self)

    @property
    def memory(self) -> Memory:
        """Long-term memory (a ``MemoryStore``): records scoped to tenant, user, agent and session, with full-text search."""
        from substrate.stores.memory_tables import Memory

        return Memory(self)

    @property
    def session_state(self) -> SessionState:
        """Small key/value state a conversation keeps across runs (a ``ShortTermMemory``)."""
        from substrate.stores.memory_tables import SessionState

        return SessionState(self)

    @property
    def tasks(self) -> Tasks:
        """Each agent's Kanban board (a ``TaskStore``)."""
        from substrate.stores.task_tables import Tasks

        return Tasks(self)

    @property
    def graph(self) -> Graph:
        """Entities and relationships (a ``GraphStore``), traversed by recursive query."""
        from substrate.stores.graph_tables import Graph

        return Graph(self)

    @property
    def vectors(self) -> Vectors:
        """Document chunks and their embeddings (a ``VectorStore``): exact search, full-text search, and both fused."""
        from substrate.stores.vector_tables import Vectors

        return Vectors(self)

    @property
    def files(self) -> Files:
        """Keyed bytes (a ``FileStore``): rows here, contents under ``files/``, written to disk before the row commits."""
        from substrate.stores.file_tables import Files

        return Files(self)

    def tenant(self, tenant: str | Scope) -> Tenant:
        """The store as one tenant sees it — every facet confined to ``tenant`` — with ``erase()`` for its data."""
        return Tenant(
            self, tenant if isinstance(tenant, Scope) else Scope(tenant_id=tenant)
        )

    @classmethod
    def at(
        cls,
        location: str | Path = "./.substrate",
        *,
        file_quota_bytes: int | None = None,
    ) -> Store:
        """The store in the folder ``location``, not yet opened: ``start()`` (or ``async with``) opens it."""
        root = Path(location).expanduser()
        return cls(
            SqliteDatabase(root / DATABASE_FILE),
            root,
            file_quota_bytes=file_quota_bytes,
        )

    async def start(self) -> None:
        """Open it, creating the folder layout and ``substrate_info``. Idempotent. Each part's tables (threads, memory, tasks,
        graph, vectors, files) are created the first time that part is used, so a store used for one of them carries
        only its tables."""
        if self._started:
            return
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        await migrate(self.database, "store", _LAYOUT)
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

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()


async def connect(location: str | Path = "./.substrate") -> Store:
    """Open — creating it if there is none — the store in the folder ``location``."""
    store = Store.at(location)
    await store.start()
    return store


__all__ = ["Store", "connect"]
