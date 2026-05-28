"""ravi.kernel.memory — Memory contract + minimal reference impl + serializers.

``SessionManager`` and any orchestration logic live in
:mod:`ravi.extensions.memory`. Concrete backends (Redis, Postgres) live in
:mod:`ravi.integrations.memory`.
"""

from ravi.kernel.memory.base_memory import BaseMemory
from ravi.kernel.memory.unbounded_memory import UnboundedMemory
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
    "BaseMemory",
    "UnboundedMemory",
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
