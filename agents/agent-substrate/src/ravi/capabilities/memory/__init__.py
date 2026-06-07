"""ravi.capabilities.memory — Concrete memory backends.

Short-term memory (ShortTermMemory protocol):
    InMemorySessionStore   — in agents layer (no external deps)
    RedisSessionStore      — Redis HASH per session, configurable TTL

Long-term memory (LongTermMemory protocol):
    PostgresMemoryStore    — full-text search via tsvector (no embeddings needed)

Vector/graph-backed implementations wrap capabilities/vector/ and capabilities/graph/.
"""

from __future__ import annotations

from ravi.capabilities.memory.redis_session_store import RedisSessionStore
from ravi.capabilities.memory.postgres_memory_store import PostgresMemoryStore

__all__ = [
    "RedisSessionStore",
    "PostgresMemoryStore",
]
