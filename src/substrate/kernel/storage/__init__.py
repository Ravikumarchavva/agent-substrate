"""In-process and local-filesystem default implementations of general-purpose
kernel storage Protocols (L1).

Consolidates what were previously three homes for "in-memory default impl of
a kernel Protocol" (``storage/``, and history providers that used to live
under ``context/``) into one — ``runtime/backends/`` stays separate since it
implements ``runtime/``-internal Protocols (``InboxProtocol``,
``SchedulerProtocol``, etc.), a different concern from these general kernel
storage Protocols (``HistoryProvider``, ``GraphStore``, ``VectorStore``,
``TaskStore``).

``InMemoryFileStore`` has no kernel Protocol counterpart today (no
``FileStore`` Protocol exists under ``kernel/storage/``) — it's general
in-memory storage, not yet standardized against a contract.
"""

from substrate.kernel.storage.graph import InMemoryGraphStore
from substrate.kernel.storage.history import (
    AncestryCheckpointResolver,
    DefaultHistoryResolver,
    HistoryProvider,
    InMemoryHistoryProvider,
    project_messages,
)
from substrate.kernel.storage.local_graph import LocalFilesystemGraphStore
from substrate.kernel.storage.local_history import LocalFilesystemHistoryProvider
from substrate.kernel.storage.local_memory_store import LocalFilesystemMemoryStore
from substrate.kernel.storage.local_object_store import (
    WorkspaceFileStore,
    WorkspacePathError,
    WorkspaceQuotaExceededError,
)
from substrate.kernel.storage.local_short_term_memory import (
    LocalFilesystemShortTermMemory,
)
from substrate.kernel.storage.local_vector import LocalFilesystemVectorStore
from substrate.kernel.storage.memory import InMemoryFileStore
from substrate.kernel.storage.tasks import TaskStore
from substrate.kernel.storage.vector import InMemoryVectorStore, cosine_similarity

__all__ = [
    "AncestryCheckpointResolver",
    "DefaultHistoryResolver",
    "HistoryProvider",
    "InMemoryFileStore",
    "InMemoryGraphStore",
    "InMemoryHistoryProvider",
    "InMemoryVectorStore",
    "LocalFilesystemGraphStore",
    "LocalFilesystemHistoryProvider",
    "LocalFilesystemMemoryStore",
    "LocalFilesystemShortTermMemory",
    "LocalFilesystemVectorStore",
    "TaskStore",
    "WorkspaceFileStore",
    "WorkspacePathError",
    "WorkspaceQuotaExceededError",
    "cosine_similarity",
    "project_messages",
]
