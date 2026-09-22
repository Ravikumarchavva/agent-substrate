"""substrate.integrations.memory — Concrete memory backends.

Short-term memory (ShortTermMemory protocol):
    RedisSessionStore      — Redis HASH per session, configurable TTL
    DurableSessionStore    — Postgres JSONB per session, durable
    CachedShortTermMemory  — durable primary + fast cache, composes the two above

Long-term memory (MemoryStore protocol):
    DurableMemoryStore     — full-text search via tsvector (no embeddings needed)
    LanceMemoryStore       — Lance-backed columnar memory store
"""

from __future__ import annotations

from substrate.integrations.memory.redis_session_store import RedisSessionStore
from substrate.integrations.memory.durable_session_store import DurableSessionStore
from substrate.integrations.memory.cached_session_store import CachedShortTermMemory
from substrate.integrations.memory.durable_memory_store import DurableMemoryStore
from substrate.integrations.memory.lance_memory_store import LanceMemoryStore, LanceLongTermMemory
from substrate.integrations.memory.factory import (
    build_short_term_memory,
    build_memory_store,
    build_long_term_memory,
)
from substrate.integrations.memory.policy import (
    MemoryExposurePolicy,
    DefaultMemoryExposurePolicy,
)
from substrate.integrations.memory.manager import MemoryManager

__all__ = [
    "RedisSessionStore",
    "DurableSessionStore",
    "CachedShortTermMemory",
    "DurableMemoryStore",
    "LanceMemoryStore",
    "LanceLongTermMemory",
    "build_short_term_memory",
    "build_memory_store",
    "build_long_term_memory",
    "MemoryExposurePolicy",
    "DefaultMemoryExposurePolicy",
    "MemoryManager",
]
