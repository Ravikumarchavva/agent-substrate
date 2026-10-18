"""substrate.stores — Persistence ports — threads, memory, vectors, graphs, files, tasks — and their folder implementations."""

from __future__ import annotations

from substrate.stores.database import (
    Database,
    StoreVersionError,
    Tx,
    migrate,
)
from substrate.stores.store import (
    Store,
    connect,
)
from substrate.stores.tenant import (
    Erased,
    Tenant,
)
from substrate.stores.blob import (
    BlobStore,
)
from substrate.stores.files import (
    FileStore,
    WorkspacePathError,
    WorkspaceQuotaExceededError,
)
from substrate.stores.graph import (
    CypherCapable,
    EntityFinder,
    Entity,
    GraphStore,
    Relationship,
    SubGraph,
)
from substrate.stores.memory import (
    ContextMemoryInjection,
    ExtractionMethod,
    MemoryCategory,
    MemoryMatch,
    MemoryNamespace,
    MemoryProvenance,
    MemoryQuery,
    MemoryRecord,
    MemoryStatus,
    MemoryStore,
    ShortTermMemory,
    TenantWide,
)
from substrate.stores.scoped import (
    bind_graph,
    bind_threads,
    bind_tasks,
    bind_vector,
    fence_objects,
)
from substrate.stores.tasks import (
    Task,
    TaskList,
    TaskStatus,
    TaskStore,
)
from substrate.stores.threads import (
    Branch,
    HistoryCheckpoint,
    ThreadStore,
    MessageNode,
)
from substrate.stores.vector import (
    Document,
    SearchResult,
    SearchableVectorStore,
    VectorStore,
)

__all__ = [
    "Database",
    "Store",
    "Tenant",
    "Erased",
    "StoreVersionError",
    "Tx",
    "connect",
    "migrate",
    "BlobStore",
    "Branch",
    "ContextMemoryInjection",
    "CypherCapable",
    "EntityFinder",
    "Document",
    "Entity",
    "ExtractionMethod",
    "GraphStore",
    "HistoryCheckpoint",
    "ThreadStore",
    "MemoryCategory",
    "MemoryMatch",
    "MemoryNamespace",
    "MemoryProvenance",
    "MemoryQuery",
    "MemoryRecord",
    "MemoryStatus",
    "MemoryStore",
    "MessageNode",
    "FileStore",
    "Relationship",
    "SearchResult",
    "ShortTermMemory",
    "SubGraph",
    "Task",
    "TaskList",
    "TaskStatus",
    "TaskStore",
    "TenantWide",
    "SearchableVectorStore",
    "VectorStore",
    "WorkspacePathError",
    "WorkspaceQuotaExceededError",
    "bind_graph",
    "bind_threads",
    "bind_tasks",
    "bind_vector",
    "fence_objects",
]
