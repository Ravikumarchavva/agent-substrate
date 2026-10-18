"""Storage: a database, and (optionally) the folder file contents are kept in beside it.

    storage = Storage.at("./.loamz")                      # SQLite file + files/ folder, zero setup
    storage = Storage(postgres(dsn, schema="acme"), "/var/lib/loamz")
    await storage.files.upload("tenants/acme/a.txt", b"hi")
    await storage.memory.save(record)
    await storage.vectors.search(...)

Each facet (``files``, ``memory``, ``vectors``) owns its tables and creates them the first time it is used
(``ensure``), so a storage used for one of them carries only its tables. Anything built on top (a document library,
an agent's thread store) uses the same two hooks: ``ensure`` for its migrations, ``run`` for a retried transaction.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING

from loamz.data.storage.errors import InvalidRequestError
from loamz.data.storage.sql import Database, Script, SqliteDatabase, Tx, migrate, version

if TYPE_CHECKING:
    from loamz.data.storage.files.tables import Files
    from loamz.data.storage.memory.tables import Memory, SessionState
    from loamz.data.storage.vectors.tables import Vectors

_ATTEMPTS = 4
DATABASE_FILE = "loamz.db"
FILES_DIR = "files"


class Storage:
    """A database and, when file contents are kept on local disk, the folder that holds them."""

    def __init__(
        self,
        database: Database,
        root: str | Path | None = None,
        *,
        file_quota_bytes: int | None = None,
    ) -> None:
        self.database = database
        self.root = Path(root) if root is not None else None
        self.file_quota_bytes = file_quota_bytes
        """Bytes a tenant may keep in ``files`` (``None``: unlimited)."""
        self.file_quota_overrides: dict[str, int] = {}
        """Per-tenant quotas that replace the default (``Files.set_quota_override``)."""
        self._started = False
        self._ensured: set[str] = set()

    @classmethod
    def at(cls, location: str | Path, *, file_quota_bytes: int | None = None) -> Storage:
        """Embedded storage in the folder ``location``: ``loamz.db`` and ``files/``. ``start()`` opens it."""
        root = Path(location).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        return cls(SqliteDatabase(root / DATABASE_FILE), root, file_quota_bytes=file_quota_bytes)

    @property
    def files_dir(self) -> Path:
        if self.root is None:
            raise InvalidRequestError(
                "this storage keeps no files (it was opened without a folder)"
            )
        return self.root / FILES_DIR

    # ── facets ───────────────────────────────────────────────────────────────
    @property
    def files(self) -> Files:
        """Keyed bytes (a ``FileStore``): rows in the database, contents under ``files/``."""
        from loamz.data.storage.files.tables import Files

        return Files(self)

    @property
    def memory(self) -> Memory:
        """Long-term memory (a ``MemoryStore``), scoped by tenant, user, agent and session, with full-text search."""
        from loamz.data.storage.memory.tables import Memory

        return Memory(self)

    @property
    def session_state(self) -> SessionState:
        """Small key/value state a conversation keeps across runs."""
        from loamz.data.storage.memory.tables import SessionState

        return SessionState(self)

    @property
    def vectors(self) -> Vectors:
        """Chunks and their embeddings (a ``VectorStore``): exact, full-text and hybrid search."""
        from loamz.data.storage.vectors.tables import Vectors

        return Vectors(self)

    # ── hooks for everything built on it ─────────────────────────────────────
    async def ensure(self, component: str, scripts: list[Script]) -> None:
        """Create or upgrade ``component``'s tables the first time it is used (``sql.migrate``). Idempotent."""
        if component in self._ensured:
            return
        await self.start()
        await migrate(self.database, component, scripts)
        self._ensured.add(component)

    async def has(self, component: str) -> bool:
        """Whether ``component``'s tables exist here, created by this process or an earlier one (asking creates
        none of them)."""
        return component in self._ensured or await version(self.database, component) > 0

    async def run[T](self, fn: Callable[[Tx], Awaitable[T]]) -> T:
        """Run ``fn`` in one transaction, again if the database aborts it for a reason a retry fixes (a deadlock)."""
        await self.start()
        for attempt in range(_ATTEMPTS):
            try:
                async with self.database.transaction() as tx:
                    return await fn(tx)
            except Exception as exc:
                if attempt + 1 < _ATTEMPTS and self.database.is_retryable(exc):
                    await asyncio.sleep(0.01 * (attempt + 1))
                    continue
                raise
        raise AssertionError("unreachable")

    # ── lifecycle ────────────────────────────────────────────────────────────
    async def start(self) -> None:
        if self._started:
            return
        if self.root is not None:
            self.files_dir.mkdir(parents=True, exist_ok=True)
        await self.database.start()
        self._started = True

    async def aclose(self) -> None:
        if self._started:
            self._started = False
            await self.database.aclose()

    async def __aenter__(self) -> Storage:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()


__all__ = ["Storage"]
