"""``Tenant``: the agent store as one tenant sees it.

``store.tenant(t)`` gives the same facets as the store, confined to ``t``: what a tenant stores is invisible to every
other tenant by any route (by id, by a hostile name), and ``erase()`` removes everything of it the agent store holds.
It is a view written once over the facets, so it holds for SQLite and PostgreSQL alike.

What Loamz Data stores for the tenant (file bytes, memory, vectors, documents) is erased by Loamz Data; the agent
store erases only its own facets.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from loamz.engine.agents.kernel.ports.files import FileStore
from loamz.engine.agents.kernel.ports.graph import GraphStore
from loamz.engine.agents.kernel.ports.tasks import TaskStore
from loamz.engine.agents.kernel.ports.threads import ThreadStore
from loamz.engine.agents.kernel.types.scope import Scope
from loamz.engine.agents.stores.scoped import (
    _tenant,
    bind_graph,
    bind_session_state,
    bind_tasks,
    bind_threads,
    fence_objects,
)
from loamz.engine.memory import ShortTermMemory

if TYPE_CHECKING:
    from loamz.engine.agents.stores.store import AgentStore


@dataclass(frozen=True, slots=True)
class Erased:
    """What ``Tenant.erase`` removed, by facet."""

    thread_nodes: int = 0
    thread_headers: int = 0
    task_boards: int = 0
    session_states: int = 0
    graph_entities: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)

    @property
    def total(self) -> int:
        return sum(asdict(self).values())


class Tenant:
    """``store.tenant(t)``: threads, tasks, session state, graph and files confined to one tenant."""

    def __init__(self, store: AgentStore, scope: Scope) -> None:
        self._store = store
        self.scope = scope

    @property
    def threads(self) -> ThreadStore:
        return bind_threads(self._store.threads, self.scope)

    @property
    def tasks(self) -> TaskStore:
        return bind_tasks(self._store.tasks, self.scope)

    @property
    def session_state(self) -> ShortTermMemory:
        return bind_session_state(self._store.session_state, self.scope)

    @property
    def graph(self) -> GraphStore:
        return bind_graph(self._store.graph, self.scope)

    @property
    def files(self) -> FileStore:
        """Absolute keys (as ``stores.workspace.layout`` builds them), each of which must lie inside
        ``tenants/<tenant>/``."""
        return fence_objects(self._store.files, self.scope)

    async def erase_conversation(self, conversation_id: str) -> Erased:
        """Remove one conversation's history, session state and task boards (not its files: those are under the
        user's prefix, and not its header: the owner's delete is a soft delete)."""
        name = f"{_tenant(self.scope)}/{conversation_id}"
        store = self._store
        return Erased(
            thread_nodes=await store.threads.erase_under(name) if await store.has("threads") else 0,
            task_boards=await store.tasks.erase_under(name) if await store.has("tasks") else 0,
            session_states=await store.session_state.erase_under(name)
            if await store.has("session")
            else 0,
        )

    async def erase(self, user_id: str | None = None) -> Erased:
        """Remove everything this tenant has in the agent store, or (``user_id``) everything of that person's
        conversations, and leave every other tenant's data untouched. A facet that was never used has no tables and
        is skipped rather than created."""
        store = self._store
        tenant_id = self.scope.tenant_id
        if user_id is not None:
            if not await store.has("thread_headers"):
                return Erased()
            ids = await store.thread_headers.owned_ids(
                tenant_id, user_id, limit=None, include_deleted=True
            )
            erased = [await self.erase_conversation(thread_id) for thread_id in ids]
            return Erased(
                thread_nodes=sum(e.thread_nodes for e in erased),
                thread_headers=await store.thread_headers.erase(tenant_id, user_id),
                task_boards=sum(e.task_boards for e in erased),
                session_states=sum(e.session_states for e in erased),
            )
        headers = (
            await store.thread_headers.erase(tenant_id) if await store.has("thread_headers") else 0
        )
        name = _tenant(self.scope)
        return Erased(
            thread_nodes=await store.threads.erase_under(name) if await store.has("threads") else 0,
            thread_headers=headers,
            task_boards=await store.tasks.erase_under(name) if await store.has("tasks") else 0,
            session_states=await store.session_state.erase_under(name)
            if await store.has("session")
            else 0,
            graph_entities=await store.graph.erase_under(name) if await store.has("graph") else 0,
        )


__all__ = ["Erased", "Tenant"]
