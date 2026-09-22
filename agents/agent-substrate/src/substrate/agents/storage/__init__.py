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

from substrate.agents.storage.history import (
    AncestryCheckpointResolver,
    DefaultHistoryResolver,
    HistoryProvider,
    InMemoryHistoryProvider,
    project_messages,
)
from substrate.agents.storage.local_history import LocalFilesystemHistoryProvider
from substrate.agents.storage.graph import InMemoryGraphStore
from substrate.agents.storage.local_graph import LocalFilesystemGraphStore
from substrate.agents.storage.memory import InMemoryFileStore
from substrate.agents.storage.tasks import TaskStore
from substrate.agents.storage.vector import InMemoryVectorStore, cosine_similarity
from substrate.agents.storage.local_vector import LocalFilesystemVectorStore

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
    "LocalFilesystemVectorStore",
    "TaskStore",
    "cosine_similarity",
    "project_messages",
]
