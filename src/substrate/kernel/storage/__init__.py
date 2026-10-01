"""The kernel's own default storage: every store here keeps its data in a folder on the local
filesystem, so it survives a restart. That folder is the least infrastructure a durable agent can
run on; Postgres, Redis, S3 and Lance versions of the same ports live in ``integrations/``.

There is deliberately no in-memory store: a store that forgets on exit cannot be what an agent's
history, memory or tasks rest on, and a test that needs one uses a ``tmp_path`` folder like any
other caller would.
"""

from __future__ import annotations

from substrate.kernel.storage.history import (
    AncestryCheckpointResolver,
    DefaultHistoryResolver,
    HistoryProvider,
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
from substrate.kernel.storage.local_tasks import LocalFilesystemTaskStore
from substrate.kernel.storage.local_vector import LocalFilesystemVectorStore, cosine_similarity

__all__ = [
    "AncestryCheckpointResolver",
    "DefaultHistoryResolver",
    "HistoryProvider",
    "LocalFilesystemGraphStore",
    "LocalFilesystemHistoryProvider",
    "LocalFilesystemMemoryStore",
    "LocalFilesystemShortTermMemory",
    "LocalFilesystemTaskStore",
    "LocalFilesystemVectorStore",
    "WorkspaceFileStore",
    "WorkspacePathError",
    "WorkspaceQuotaExceededError",
    "cosine_similarity",
    "project_messages",
]
