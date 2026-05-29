"""ravi.kernel.memory — History contract + serializers + lineage types.

The ``InMemoryHistoryProvider`` reference implementation lives in
:mod:`ravi.fabric.memory.in_memory` (L1), as does the ``TieredHistoryProvider``
composition. Context strategies live in :mod:`ravi.reasoning.memory`. Concrete
backends (Redis, Postgres) live in :mod:`ravi.integrations.memory`.
"""

from ravi.kernel.memory.history_provider import (
    CachedHistoryProvider,
    HistoryProvider,
    PersistentHistoryProvider,
)
from ravi.kernel.memory.memory_scope import MemoryScope
from ravi.kernel.memory.message_serializer import (
    serialize_message,
    deserialize_message,
    serialize_messages,
    deserialize_messages,
)
from ravi.kernel.memory._lineage import (
    LineageNotFoundError,
    LineageRecord,
    LineageStore,
    ProvenanceTag,
    StorageTier,
)

__all__ = [
    "HistoryProvider",
    "CachedHistoryProvider",
    "PersistentHistoryProvider",
    "MemoryScope",
    "serialize_message",
    "deserialize_message",
    "serialize_messages",
    "deserialize_messages",
    # S9 lineage
    "LineageNotFoundError",
    "LineageRecord",
    "LineageStore",
    "ProvenanceTag",
    "StorageTier",
]
