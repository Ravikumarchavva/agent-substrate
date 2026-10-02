"""substrate.stores — Persistence ports — threads, memory, vectors, graphs, files, tasks — and their folder implementations."""

from __future__ import annotations

from substrate.stores.blob import (
    BlobStore,
)
from substrate.stores.files import (
    ObjectStore,
)
from substrate.stores.graph import (
    CypherCapable,
    Entity,
    GraphStore,
    Relationship,
    SubGraph,
)
from substrate.stores.local.files import (
    WorkspaceFileStore,
    WorkspacePathError,
    WorkspaceQuotaExceededError,
)
from substrate.stores.local.graph import (
    LocalFilesystemGraphStore,
)
from substrate.stores.local.memory import (
    LocalFilesystemMemoryStore,
)
from substrate.stores.local.short_term_memory import (
    LocalFilesystemShortTermMemory,
)
from substrate.stores.local.tasks import (
    LocalFilesystemTaskStore,
)
from substrate.stores.local.threads import (
    LocalFilesystemHistoryProvider,
)
from substrate.stores.local.vector import (
    LocalFilesystemVectorStore,
    cosine_similarity,
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
    bind_history,
    bind_objects,
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
    HistoryProvider,
    MessageNode,
)
from substrate.stores.vector import (
    Document,
    SearchResult,
    VectorStore,
)

__all__ = [
    "BlobStore",
    "Branch",
    "ContextMemoryInjection",
    "CypherCapable",
    "Document",
    "Entity",
    "ExtractionMethod",
    "GraphStore",
    "HistoryCheckpoint",
    "HistoryProvider",
    "LocalFilesystemGraphStore",
    "LocalFilesystemHistoryProvider",
    "LocalFilesystemMemoryStore",
    "LocalFilesystemShortTermMemory",
    "LocalFilesystemTaskStore",
    "LocalFilesystemVectorStore",
    "MemoryCategory",
    "MemoryMatch",
    "MemoryNamespace",
    "MemoryProvenance",
    "MemoryQuery",
    "MemoryRecord",
    "MemoryStatus",
    "MemoryStore",
    "MessageNode",
    "ObjectStore",
    "Relationship",
    "SearchResult",
    "ShortTermMemory",
    "SubGraph",
    "Task",
    "TaskList",
    "TaskStatus",
    "TaskStore",
    "TenantWide",
    "VectorStore",
    "WorkspaceFileStore",
    "WorkspacePathError",
    "WorkspaceQuotaExceededError",
    "bind_graph",
    "bind_history",
    "bind_objects",
    "bind_tasks",
    "bind_vector",
    "cosine_similarity",
    "fence_objects",
]
