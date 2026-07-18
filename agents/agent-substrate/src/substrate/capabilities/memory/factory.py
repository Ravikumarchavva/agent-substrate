"""Default memory construction — pick short-term or long-term, get Postgres,
add a cache only if you want one.

Mirrors ``ContextConfig.default()``'s convention: a batteries-included
default a caller doesn't have to hand-assemble. Both kinds default to
Postgres because it's the one backend every deployment already has and
because both kernel protocols were designed against it (``ShortTermMemory``'s
docstring names Postgres JSONB; ``LongTermMemory``'s implementation *is*
Postgres full-text). Retrieval can grow later — swap in a vector- or
graph-backed ``LongTermMemory`` — without changing the call site, since
callers only ever depend on the Protocol, never the concrete class.

Usage::

    # Postgres only, no cache
    stm = await build_short_term_memory(database_url)

    # Postgres + Redis cache in front of it
    stm = await build_short_term_memory(database_url, redis_url=redis_url)

    ltm = await build_long_term_memory(database_url)
"""

from __future__ import annotations

from substrate.kernel.storage.memory import LongTermMemory, ShortTermMemory


async def build_short_term_memory(
    database_url: str,
    *,
    redis_url: str | None = None,
    ttl: int = 3600,
) -> ShortTermMemory:
    """Durable ShortTermMemory (Postgres), optionally fronted by a Redis cache.

    Pass ``redis_url`` to get ``CachedShortTermMemory`` — durable-first
    writes, cache-first reads, self-healing on a cache miss. Omit it for a
    Postgres-only setup with no cache.
    """
    from substrate.capabilities.memory.postgres_session_store import (
        PostgresSessionStore,
    )

    primary = PostgresSessionStore(database_url)
    await primary.connect()
    if redis_url is None:
        return primary

    from substrate.capabilities.memory.cached_session_store import (
        CachedShortTermMemory,
    )
    from substrate.capabilities.memory.redis_session_store import RedisSessionStore

    cache = RedisSessionStore(redis_url=redis_url, ttl=ttl)
    await cache.connect()
    return CachedShortTermMemory(primary=primary, cache=cache)


async def build_long_term_memory(database_url: str) -> LongTermMemory:
    """Durable LongTermMemory — Postgres full-text search.

    No cache variant: the read path is arbitrary-query search, which a
    key-value cache doesn't map onto the way flat session state does. A
    future semantic-cache-style wrapper would be a different mechanism, not
    this one with a flag flipped.
    """
    from substrate.capabilities.memory.postgres_memory_store import (
        PostgresMemoryStore,
    )

    store = PostgresMemoryStore(database_url)
    await store.connect()
    await store.create_tables()
    return store


__all__ = ["build_short_term_memory", "build_long_term_memory"]
