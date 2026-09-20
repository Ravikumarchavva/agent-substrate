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
    database_url: str = "",
    *,
    redis_url: str | None = None,
    ttl: int = 3600,
    local_path: str = "./data/db/memory/short_term",
) -> ShortTermMemory:
    """Durable ShortTermMemory.

    Uses PostgreSQL (+ optional Redis cache) when database_url is provided.
    When database_url is empty, uses LocalFileSessionStore (atomic JSON files
    in local_path) so session state persists across restarts without external services.
    """
    if database_url:
        from substrate.capabilities.memory.durable_session_store import (
            DurableSessionStore,
        )

        primary = DurableSessionStore(database_url)
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

    from substrate.capabilities.memory.local_session_store import LocalFileSessionStore

    store = LocalFileSessionStore(root=local_path)
    await store.connect()
    return store


async def build_long_term_memory(
    database_url: str = "",
    *,
    local_path: str = "./data/db/memory/long_term",
) -> LongTermMemory:
    """Durable LongTermMemory.

    Uses PostgreSQL full-text search when database_url is provided.
    When database_url is empty, uses embedded LanceLongTermMemory in local_path
    so user facts and preferences persist durably without PostgreSQL.
    """
    if database_url:
        from substrate.capabilities.memory.durable_memory_store import (
            DurableMemoryStore,
        )

        pg_store = DurableMemoryStore(database_url)
        await pg_store.connect()
        await pg_store.create_tables()
        return pg_store

    from substrate.capabilities.memory.lance_memory_store import LanceLongTermMemory

    return LanceLongTermMemory(path=local_path)


__all__ = ["build_short_term_memory", "build_long_term_memory"]
