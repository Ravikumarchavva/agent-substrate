"""The agent's own state on loamz.engine: threads (+ headers), tasks, session state, the graph, workspaces.

``AgentStore`` is one database and its facets; ``store.tenant(t)`` is the same, confined to a tenant. The facet
contracts (``ThreadStore``, ``TaskStore``, ``GraphStore``, ``WorkspaceStore``…) are kernel ports
(``loamz.engine.agents.kernel.ports``); these are their one implementation, written once in SQL for SQLite and PostgreSQL.
"""

from __future__ import annotations

from loamz.engine.agents.stores.headers import ThreadHeader, ThreadHeaders, ThreadPage
from loamz.engine.agents.stores.scoped import (
    bind_graph,
    bind_session_state,
    bind_tasks,
    bind_threads,
    fence_objects,
)
from loamz.engine.agents.stores.store import AgentStore
from loamz.engine.agents.stores.tenant import Erased, Tenant

__all__ = [
    "AgentStore",
    "Erased",
    "Tenant",
    "ThreadHeader",
    "ThreadHeaders",
    "ThreadPage",
    "bind_graph",
    "bind_session_state",
    "bind_tasks",
    "bind_threads",
    "fence_objects",
]
