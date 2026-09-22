"""Default memory construction — pick short-term or long-term, get Postgres,
add a cache only if you want one.
"""

from __future__ import annotations

from substrate.kernel.storage.memory import MemoryStore, ShortTermMemory


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
        from substrate.integrations.memory.durable_session_store import (
            DurableSessionStore,
        )

        primary = DurableSessionStore(database_url)
        await primary.connect()
        if redis_url is None:
            return primary

        from substrate.integrations.memory.cached_session_store import (
            CachedShortTermMemory,
        )
        from substrate.integrations.memory.redis_session_store import RedisSessionStore

        cache = RedisSessionStore(redis_url=redis_url, ttl=ttl)
        await cache.connect()
        return CachedShortTermMemory(primary=primary, cache=cache)

    from substrate.integrations.memory.local_session_store import LocalFileSessionStore

    store = LocalFileSessionStore(root=local_path)
    await store.connect()
    return store


async def build_memory_store(
    database_url: str = "",
    *,
    local_path: str = "./data/db/memory/long_term",
) -> MemoryStore:
    """Durable MemoryStore.

    Uses PostgreSQL full-text search when database_url is provided.
    When database_url is empty, uses embedded LanceMemoryStore in local_path
    so user facts and preferences persist durably without PostgreSQL.
    """
    if database_url:
        from substrate.integrations.memory.durable_memory_store import (
            DurableMemoryStore,
        )

        pg_store = DurableMemoryStore(database_url)
        await pg_store.connect()
        await pg_store.create_tables()
        return pg_store

    from substrate.integrations.memory.lance_memory_store import LanceMemoryStore

    return LanceMemoryStore(path=local_path)


# Compatibility alias
build_long_term_memory = build_memory_store

__all__ = ["build_short_term_memory", "build_memory_store", "build_long_term_memory"]
