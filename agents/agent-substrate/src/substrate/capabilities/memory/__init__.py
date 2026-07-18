"""substrate.capabilities.memory — Concrete memory backends.

Short-term memory (ShortTermMemory protocol):
    RedisSessionStore      — Redis HASH per session, configurable TTL
    PostgresSessionStore   — Postgres JSONB per session, durable
    CachedShortTermMemory  — durable primary + fast cache, composes the two above

Long-term memory (LongTermMemory protocol):
    PostgresMemoryStore    — full-text search via tsvector (no embeddings needed)

Vector/graph-backed implementations wrap capabilities/vector/ and capabilities/graph/.

Don't want to hand-assemble the above? ``build_short_term_memory()`` /
``build_long_term_memory()`` (factory.py) give you the default — Postgres,
optionally cached — in one call.
"""

from __future__ import annotations

from substrate.capabilities.memory.redis_session_store import RedisSessionStore
from substrate.capabilities.memory.postgres_session_store import PostgresSessionStore
from substrate.capabilities.memory.cached_session_store import CachedShortTermMemory
from substrate.capabilities.memory.postgres_memory_store import PostgresMemoryStore
from substrate.capabilities.memory.factory import (
    build_short_term_memory,
    build_long_term_memory,
)

__all__ = [
    "RedisSessionStore",
    "PostgresSessionStore",
    "CachedShortTermMemory",
    "PostgresMemoryStore",
    "build_short_term_memory",
    "build_long_term_memory",
]
