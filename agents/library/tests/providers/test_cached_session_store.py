from __future__ import annotations

import tempfile

import pytest
from tests._stores import agent_store_at

from loamz.agents.providers.redis.cache import CachedShortTermMemory
from loamz.agents.providers.redis.short_term import RedisSessionStore

pytestmark = [pytest.mark.requires_redis]


async def _make_pair() -> tuple[object, RedisSessionStore]:
    from tests.conftest import REDIS_URL

    primary = agent_store_at(tempfile.mkdtemp(prefix="loamz-cached-")).session_state
    cache = RedisSessionStore(redis_url=REDIS_URL, ttl=60)
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
