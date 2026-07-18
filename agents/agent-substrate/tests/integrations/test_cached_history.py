from __future__ import annotations

import pytest
import redis.exceptions

from substrate.capabilities.history import CachedHistoryProvider, RedisHistoryProvider
from substrate.agents.context import InMemoryHistoryProvider
from substrate.kernel import AgentId
from substrate.kernel.core.content import ChatMessage, TextBlock

pytestmark = [pytest.mark.requires_redis]


def _msg(text: str) -> ChatMessage:
    return ChatMessage(role="user", content=[TextBlock(text=text)])


@pytest.mark.asyncio
async def test_write_propagates_to_cache():
    provider = RedisHistoryProvider(redis_url="redis://localhost:6379/0", ttl=60)
    try:
        await provider.connect()
    except (redis.exceptions.ConnectionError, OSError) as e:
        pytest.skip(f"Redis is not available: {e}")

    agent_id = AgentId(type="user", key="cached-write-test")
    session_id = "cached-write-sess"
    cached = CachedHistoryProvider(cache=provider)
    try:
        await cached.clear(agent_id, session_id=session_id)
        await cached.append(agent_id, _msg("hello"), session_id=session_id)

        assert await cached.count_messages(agent_id, session_id=session_id) == 1
        msgs = await cached.get_messages(agent_id, session_id=session_id)
        assert len(msgs) == 1
        assert msgs[0].content[0].text == "hello"  # type: ignore[union-attr]

        # underlying cache actually received the write, not just the wrapper
        assert await provider.count_messages(agent_id, session_id=session_id) == 1
    finally:
        await cached.clear(agent_id, session_id=session_id)
        await provider.disconnect()


@pytest.mark.asyncio
async def test_cache_miss_reseeds_from_cold_store_and_repopulates():
    provider = RedisHistoryProvider(redis_url="redis://localhost:6379/0", ttl=60)
    try:
        await provider.connect()
    except (redis.exceptions.ConnectionError, OSError) as e:
        pytest.skip(f"Redis is not available: {e}")

    agent_id = AgentId(type="user", key="cached-miss-test")
    session_id = "cached-miss-sess"

    cold_store_messages = [_msg("from cold store 1"), _msg("from cold store 2")]

    async def reseed() -> list[ChatMessage]:
        return cold_store_messages

    cached = CachedHistoryProvider(
        cache=provider, reseed=reseed, cold_store_name="fake cold store"
    )
    try:
        await provider.clear(agent_id, session_id=session_id)
        assert await provider.count_messages(agent_id, session_id=session_id) == 0

        # cache is cold — reads through to reseed() and repopulates the cache
        msgs = await cached.get_messages(agent_id, session_id=session_id)
        assert [m.content[0].text for m in msgs] == [  # type: ignore[union-attr]
            "from cold store 1",
            "from cold store 2",
        ]

        # the cache itself is now populated — a second read doesn't need reseed()
        assert await provider.count_messages(agent_id, session_id=session_id) == 2
    finally:
        await provider.clear(agent_id, session_id=session_id)
        await provider.disconnect()


@pytest.mark.asyncio
async def test_no_reseed_is_a_thin_passthrough():
    cache = InMemoryHistoryProvider()
    cached = CachedHistoryProvider(cache=cache)
    agent_id = AgentId(type="user", key="passthrough-test")
    session_id = "passthrough-sess"

    assert await cached.get_messages(agent_id, session_id=session_id) == []
    await cached.append(agent_id, _msg("only message"), session_id=session_id)
    msgs = await cached.get_messages(agent_id, session_id=session_id)
    assert len(msgs) == 1


@pytest.mark.asyncio
async def test_clear_run_propagates_to_cache():
    provider = RedisHistoryProvider(redis_url="redis://localhost:6379/0", ttl=60)
    try:
        await provider.connect()
    except (redis.exceptions.ConnectionError, OSError) as e:
        pytest.skip(f"Redis is not available: {e}")

    agent_id = AgentId(type="user", key="cached-clear-run-test")
    session_id = "cached-clear-run-sess"
    cached = CachedHistoryProvider(cache=provider)
    try:
        await cached.clear(agent_id, session_id=session_id)
        await cached.append(agent_id, _msg("run-a"), session_id=session_id, run_id="a")
        await cached.append(agent_id, _msg("run-b"), session_id=session_id, run_id="b")

        await cached.clear_run(agent_id, session_id=session_id, run_id="a")
        remaining = await cached.get_messages(agent_id, session_id=session_id)
        assert len(remaining) == 1
        assert remaining[0].content[0].text == "run-b"  # type: ignore[union-attr]
    finally:
        await cached.clear(agent_id, session_id=session_id)
        await provider.disconnect()
