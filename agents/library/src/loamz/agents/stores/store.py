"""AgentStore: the agent's own state, as facets of one database.

    store = AgentStore.at("./.loamz/agents.db")        # SQLite file; or AgentStore(postgres(dsn, schema=...))
    async with store:
        runtime = Runtime(store)

Everything the engine remembers about agents lives in one ``loamz.data.storage.sql`` database, so a turn can change
several parts in one transaction: runs and their journal (``kernel.runtime.persistence``), threads (the conversation
DAG) and their headers, task boards, session state, the knowledge graph and workspace snapshots. Each facet owns its
tables as append-only migrations and creates them the first time it is used, so a store used for one thing carries
only that thing's tables.

What durably stores *data* (file bytes, long-term memory, vectors, documents) belongs to Loamz Data; the store takes
those by interface (``files=``) where an agent facet needs them, and never owns them.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING

from loamz.agents.kernel.ports.files import FileStore
from loamz.agents.kernel.types.scope import Scope
from loamz.agents.stores.tenant import Tenant
from loamz.data.storage.sql import Database, Tx, migrate, sqlite

if TYPE_CHECKING:
    from loamz.agents.stores.graph import Graph
    from loamz.agents.stores.headers import ThreadHeaders
    from loamz.agents.stores.session import SessionState
    from loamz.agents.stores.tasks import Tasks
    from loamz.agents.stores.threads import Threads
    from loamz.agents.stores.workspace.tables import Workspaces

_ATTEMPTS = 4


class AgentStore:
    """A database and the agent facets over it."""

    def __init__(self, database: Database, *, files: FileStore | None = None) -> None:
        self.database = database
        self._files = files
        self._started = False
        self._ensured: set[str] = set()

    @classmethod
    def at(cls, path: str | Path, *, files: FileStore | None = None) -> AgentStore:
        """The store in the SQLite file ``path`` (its folder is created), not yet opened."""
        location = Path(path).expanduser()
        location.parent.mkdir(parents=True, exist_ok=True)
        return cls(sqlite(str(location)), files=files)

    async def ensure(self, component: str, scripts: list) -> None:
        """Create or upgrade ``component``'s tables the first time it is used. Idempotent."""
        if component in self._ensured:
            return
        await self.start()
        await migrate(self.database, component, scripts)
        self._ensured.add(component)

    async def has(self, component: str) -> bool:
        """Whether ``component``'s tables exist (made by this process or an earlier one). Creates nothing."""
        if component in self._ensured:
            return True
        await self.start()
        await migrate(
            self.database, "agent_store", []
        )  # nothing to apply: makes sure the migrations table exists
        async with self.database.transaction() as tx:
            rows = await tx.fetchall(
                "SELECT version FROM loamz_migrations WHERE component = ?", component
            )
        return bool(rows)

    # ── facets ──────────────────────────────────────────────────────────────
    # A fresh handle each time: a facet holds nothing but the store, so a dropped store is freed at once.

    @property
    def threads(self) -> Threads:
        """The conversation DAG (a ``ThreadStore``)."""
        from loamz.agents.stores.threads import Threads

        return Threads(self)

    @property
    def thread_headers(self) -> ThreadHeaders:
        """Names, owners, tags and pinned/archived/locked state of threads, keyed by tenant."""
        from loamz.agents.stores.headers import ThreadHeaders

        return ThreadHeaders(self)

    @property
    def session_state(self) -> SessionState:
        """Small key/value state a conversation keeps across runs (a ``ShortTermMemory``)."""
        from loamz.agents.stores.session import SessionState

        return SessionState(self)

    @property
    def tasks(self) -> Tasks:
        """Each agent's Kanban boards (a ``TaskStore``)."""
        from loamz.agents.stores.tasks import Tasks

        return Tasks(self)

    @property
    def graph(self) -> Graph:
        """Entities and relationships (a ``GraphStore``), traversed by recursive query."""
        from loamz.agents.stores.graph import Graph

        return Graph(self)

    @property
    def workspaces(self) -> Workspaces:
        """Workspace snapshot trees and their branch heads (a ``WorkspaceStore``)."""
        from loamz.agents.stores.workspace.tables import Workspaces

        return Workspaces(self)

    @property
    def files(self) -> FileStore:
        """The keyed byte store this agent store was given (Loamz Data's, or a test double)."""
        if self._files is None:
            raise LookupError(
                "this AgentStore was built without a file store (AgentStore(..., files=...))"
            )
        return self._files

    def tenant(self, tenant: str | Scope) -> Tenant:
        """The store as one tenant sees it: every facet confined to it, with ``erase()`` for its data."""
        return Tenant(self, tenant if isinstance(tenant, Scope) else Scope(tenant_id=tenant))

    # ── lifecycle ───────────────────────────────────────────────────────────

    async def start(self) -> None:
        if not self._started:
            await self.database.start()
            self._started = True

    async def run[T](self, fn: Callable[[Tx], Awaitable[T]]) -> T:
        """Run ``fn`` in one transaction, again from the start if the database aborted it for a reason a retry fixes
        (a deadlock between two writers)."""
        await self.start()
        for attempt in range(_ATTEMPTS):
            try:
                async with self.database.transaction() as tx:
                    return await fn(tx)
            except Exception as exc:  # noqa: BLE001 - retried only when the database says it is retryable
                if attempt + 1 < _ATTEMPTS and self.database.is_retryable(exc):
                    await asyncio.sleep(0.01 * (attempt + 1))
                    continue
                raise
        raise AssertionError("unreachable")

    async def aclose(self) -> None:
        if self._started:
            self._started = False
            await self.database.aclose()

    async def __aenter__(self) -> AgentStore:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()


__all__ = ["AgentStore"]
