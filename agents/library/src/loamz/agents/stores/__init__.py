"""The agent's own state on loamz.data.storage: threads (+ headers), tasks, session state, the graph, workspaces.

``AgentStore`` is one database and its facets; ``store.tenant(t)`` is the same, confined to a tenant. The facet
contracts (``ThreadStore``, ``TaskStore``, ``GraphStore``, ``WorkspaceStore``…) are kernel ports
(``loamz.agents.kernel.ports``); these are their one implementation, written once in SQL for SQLite and PostgreSQL.
"""

from __future__ import annotations

from loamz.agents.stores.headers import ThreadHeader, ThreadHeaders, ThreadPage
from loamz.agents.stores.scoped import bind_graph, bind_tasks, bind_threads, fence_objects
from loamz.agents.stores.store import AgentStore
from loamz.agents.stores.tenant import Erased, Tenant

__all__ = [
    "AgentStore",
    "Erased",
    "Tenant",
    "ThreadHeader",
    "ThreadHeaders",
    "ThreadPage",
    "bind_graph",
    "bind_tasks",
    "bind_threads",
    "fence_objects",
]
