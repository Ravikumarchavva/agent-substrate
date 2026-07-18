from __future__ import annotations

import os

import pytest

from substrate.capabilities.memory import (
    CachedShortTermMemory,
    DurableSessionStore,
    RedisSessionStore,
)

pytestmark = [pytest.mark.requires_redis, pytest.mark.requires_postgres]


async def _make_pair() -> tuple[DurableSessionStore, RedisSessionStore]:
    db_url = os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb"
    )
    primary = DurableSessionStore(db_url)
    await primary.connect()
    cache = RedisSessionStore(redis_url="redis://localhost:6379/0", ttl=60)
    await cache.connect()
    return primary, cache


@pytest.mark.asyncio
async def test_write_propagates_to_both_backends():
    primary, cache = await _make_pair()
    session_id = "cached-stm-write-sess"
    merged = CachedShortTermMemory(primary=primary, cache=cache)
    try:
        await merged.clear(session_id)
        await merged.set_state(session_id, {"language": "Python"})

        assert await merged.get_state(session_id) == {"language": "Python"}
        # both backends actually received the write, not just the wrapper
        assert await primary.get_state(session_id) == {"language": "Python"}
        assert await cache.get_state(session_id) == {"language": "Python"}
    finally:
        await merged.clear(session_id)
        await merged.disconnect()


@pytest.mark.asyncio
async def test_cache_miss_reads_through_to_primary_and_repopulates():
    primary, cache = await _make_pair()
    session_id = "cached-stm-miss-sess"
    try:
        await primary.clear(session_id)
        await cache.clear(session_id)

        # populate only the durable primary, simulating a cache eviction
        await primary.set_state(session_id, {"preferred_language": "Python"})
        assert await cache.get_state(session_id) == {}

        merged = CachedShortTermMemory(primary=primary, cache=cache)
        state = await merged.get_state(session_id)
        assert state == {"preferred_language": "Python"}

        # the cache is now repopulated from the primary
        assert await cache.get_state(session_id) == {"preferred_language": "Python"}
    finally:
        await primary.clear(session_id)
        await cache.clear(session_id)
        await primary.disconnect()
        await cache.disconnect()


@pytest.mark.asyncio
async def test_update_state_merges_without_clobbering():
    primary, cache = await _make_pair()
    session_id = "cached-stm-update-sess"
    merged = CachedShortTermMemory(primary=primary, cache=cache)
    try:
        await merged.clear(session_id)
        await merged.set_state(session_id, {"a": 1})
        await merged.update_state(session_id, {"b": 2})

        assert await merged.get_state(session_id) == {"a": 1, "b": 2}
    finally:
        await merged.clear(session_id)
        await merged.disconnect()


@pytest.mark.asyncio
async def test_clear_propagates_to_both_backends():
    primary, cache = await _make_pair()
    session_id = "cached-stm-clear-sess"
    merged = CachedShortTermMemory(primary=primary, cache=cache)
    try:
        await merged.set_state(session_id, {"x": 1})
        await merged.clear(session_id)

        assert await primary.get_state(session_id) == {}
        assert await cache.get_state(session_id) == {}
    finally:
        await merged.disconnect()
