"""``Tenant`` — the store as one tenant sees it.

``store.tenant(t)`` gives the same facets as the store, confined to ``t``: what a tenant stores is invisible to
every other tenant by any route (by id, by a hostile name), and ``erase()`` removes everything it ever stored.
It is a view, written once over the facets — it holds for the folder store and for any later backend alike.

Long-term memory is not wrapped: every call already names its ``MemoryNamespace``, whose tenant the store enforces.
Run state (session state, the run journal) is keyed by session, not tenant, and is erased with the session.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from substrate.stores.graph import GraphStore
from substrate.stores.memory import MemoryNamespace
from substrate.stores.scoped import (
    _FencedFileStore,
    _tenant,
    bind_graph,
    bind_tasks,
    bind_threads,
    bind_vector,
)
from substrate.stores.tasks import TaskStore
from substrate.stores.threads import ThreadStore
from substrate.stores.vector import VectorStore
from substrate.types.scope import Scope

if TYPE_CHECKING:
    from substrate.stores.store import Store


@dataclass(frozen=True, slots=True)
class Erased:
    """What ``Tenant.erase`` removed, by facet."""

    thread_nodes: int = 0
    task_boards: int = 0
    vectors: int = 0
    graph_entities: int = 0
    files: int = 0
    memories: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)

    @property
    def total(self) -> int:
        return sum(asdict(self).values())


class Tenant:
    """``store.tenant(t)``: threads, tasks, vectors, graph and files confined to one tenant."""

    def __init__(self, store: Store, scope: Scope) -> None:
        self._store = store
        self.scope = scope

    @property
    def threads(self) -> ThreadStore:
        return bind_threads(self._store.threads, self.scope)

    @property
    def tasks(self) -> TaskStore:
        return bind_tasks(self._store.tasks, self.scope)

    @property
    def vectors(self) -> VectorStore:
        return bind_vector(self._store.vectors, self.scope)

    @property
    def graph(self) -> GraphStore:
        return bind_graph(self._store.graph, self.scope)

    @property
    def files(self) -> _FencedFileStore:
        """Absolute keys (as ``workspace.layout`` builds them), each of which must lie inside ``tenants/<tenant>/``."""
        return _FencedFileStore(self._store.files, self.scope)

    async def erase_conversation(self, conversation_id: str) -> Erased:
        """Remove one conversation's history and task boards (not its files: those are under the user's prefix)."""
        name = f"{_tenant(self.scope)}/{conversation_id}"
        store = self._store
        return Erased(
            thread_nodes=await store.threads.erase_under(name)
            if await store.has("threads")
            else 0,
            task_boards=await store.tasks.erase_under(name)
            if await store.has("tasks")
            else 0,
        )

    async def erase(self) -> Erased:
        """Remove everything this tenant has stored — threads, tasks, vectors, graph, files, memory — and leave every
        other tenant's data untouched. The words of what was erased leave the search indexes and the database file.

        A part that was never used has no tables and nothing to erase; it is skipped rather than created."""
        name = _tenant(self.scope)
        store = self._store
        return Erased(
            thread_nodes=await store.threads.erase_under(name)
            if await store.has("threads")
            else 0,
            task_boards=await store.tasks.erase_under(name)
            if await store.has("tasks")
            else 0,
            vectors=await store.vectors.erase_under(name)
            if await store.has("vectors")
            else 0,
            graph_entities=await store.graph.erase_under(name)
            if await store.has("graph")
            else 0,
            files=await self.files.erase() if await store.has("files") else 0,
            memories=await store.memory.erase(
                MemoryNamespace(tenant_id=self.scope.tenant_id)
            )
            if await store.has("memory")
            else 0,
        )
