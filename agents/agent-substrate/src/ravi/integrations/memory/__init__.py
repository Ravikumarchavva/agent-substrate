"""ravi.integrations.memory — Concrete memory backends.

Short-term memory (ShortTermMemory protocol):
    InMemorySessionStore   — in agents layer (no external deps)
    RedisSessionStore      — Redis HASH per session, configurable TTL

Long-term memory (LongTermMemory protocol):
    PostgresMemoryStore    — full-text search via tsvector (no embeddings needed)

Vector/graph-backed implementations wrap integrations/vector/ and integrations/graph/.
"""

from __future__ import annotations

from ravi.integrations.memory.redis_session_store import RedisSessionStore
from ravi.integrations.memory.postgres_memory_store import PostgresMemoryStore

__all__ = [
    "RedisSessionStore",
    "PostgresMemoryStore",
]
