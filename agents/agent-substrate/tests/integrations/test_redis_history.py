from __future__ import annotations

import pytest
import redis.exceptions

from substrate.capabilities.history import RedisHistoryProvider
from substrate.kernel import AgentId
from substrate.kernel.core.content import ChatMessage, TextBlock

pytestmark = [pytest.mark.requires_redis]


@pytest.mark.asyncio
async def test_redis_history_provider():
    provider = RedisHistoryProvider(redis_url="redis://localhost:6379/0", ttl=60)
    try:
        await provider.connect()
    except (redis.exceptions.ConnectionError, OSError) as e:
        pytest.skip(f"Redis is not available: {e}")

    try:
        agent_id = AgentId(type="user", key="123")
        session_id = "test-session-001"
        run_id = "run-abc"

        await provider.clear(agent_id, session_id=session_id)
        assert await provider.count_messages(agent_id, session_id=session_id) == 0

        msg = ChatMessage(role="user", content=[TextBlock(text="redis message")])
        await provider.append(agent_id, msg, session_id=session_id, run_id=run_id)

        assert await provider.count_messages(agent_id, session_id=session_id) == 1
        msgs = await provider.get_messages(agent_id, session_id=session_id)
        assert len(msgs) == 1
        assert msgs[0].role == "user"
        assert msgs[0].content[0].text == "redis message"
        assert msgs[0].metadata.get("run_id") == run_id

        await provider.clear(agent_id, session_id=session_id)
        assert await provider.count_messages(agent_id, session_id=session_id) == 0
    finally:
        await provider.disconnect()


@pytest.mark.asyncio
async def test_redis_clear_run():
    provider = RedisHistoryProvider(redis_url="redis://localhost:6379/0", ttl=60)
    try:
        await provider.connect()
    except (redis.exceptions.ConnectionError, OSError) as e:
        pytest.skip(f"Redis is not available: {e}")

    try:
        agent_id = AgentId(type="user", key="clear-run-test")
        session_id = "sess"

        await provider.clear(agent_id, session_id=session_id)

        m1 = ChatMessage(role="user", content=[TextBlock(text="run-a msg")])
        m2 = ChatMessage(role="user", content=[TextBlock(text="run-b msg")])
        await provider.append(agent_id, m1, session_id=session_id, run_id="run-a")
        await provider.append(agent_id, m2, session_id=session_id, run_id="run-b")

        await provider.clear_run(agent_id, session_id=session_id, run_id="run-a")
        remaining = await provider.get_messages(agent_id, session_id=session_id)
        assert len(remaining) == 1
        assert remaining[0].content[0].text == "run-b msg"
    finally:
        await provider.clear(agent_id, session_id=session_id)
        await provider.disconnect()


@pytest.mark.asyncio
async def test_try_acquire_seed_lock_is_exclusive():
    """Only one caller wins the seed-lock race — the guard against the
    double-seed truncation bug (see try_acquire_seed_lock's docstring)."""
    provider = RedisHistoryProvider(redis_url="redis://localhost:6379/0", ttl=60)
    try:
        await provider.connect()
    except (redis.exceptions.ConnectionError, OSError) as e:
        pytest.skip(f"Redis is not available: {e}")

    agent_id = AgentId(type="user", key="seedlock-test")
    session_id = "seedlock-sess"
    try:
        # Clean slate: delete any leftover lock key from a prior run.
        client = provider._require_client()  # type: ignore[attr-defined]
        await client.delete(provider._seed_lock_key(agent_id, session_id))  # type: ignore[attr-defined]

        first = await provider.try_acquire_seed_lock(agent_id, session_id, ttl=5)
        second = await provider.try_acquire_seed_lock(agent_id, session_id, ttl=5)
        assert first is True
        assert second is False
    finally:
        await provider.disconnect()


@pytest.mark.asyncio
async def test_cached_history_provider_concurrent_reads_seed_once():
    """Two concurrent CachedHistoryProvider reads for the same cold session
    (the real-world race: two replicas, or two racing requests) must seed
    exactly once — a double-seed would double every persisted message."""
    from substrate.agents.factory import rebuild_messages_from_steps
    from substrate.capabilities.history.cached_history import CachedHistoryProvider

    provider = RedisHistoryProvider(redis_url="redis://localhost:6379/0", ttl=60)
    try:
        await provider.connect()
    except (redis.exceptions.ConnectionError, OSError) as e:
        pytest.skip(f"Redis is not available: {e}")

    session_id = "concurrent-seed-sess"
    agent_id = AgentId(type="assistant", key=session_id)
    try:
        await provider.clear(agent_id, session_id=session_id)

        steps = [
            {"type": "user_message", "input": "hello"},
            {"type": "assistant_message", "output": "hi there"},
        ]
        # rebuild_messages_from_steps also prepends one system message
        # whenever system_instructions is non-empty.
        expected_count = len(steps) + 1

        async def _reseed():
            return await rebuild_messages_from_steps(steps, "You are helpful.")

        cached = CachedHistoryProvider(
            cache=provider, reseed=_reseed, cold_store_name="test cold store"
        )

        import asyncio

        results = await asyncio.gather(
            *[cached.get_messages(agent_id, session_id=session_id) for _ in range(5)]
        )
        assert all(len(r) == expected_count for r in results)

        count = await provider.count_messages(agent_id, session_id=session_id)
        assert count == expected_count, (
            f"expected exactly {expected_count} messages from one seed, got "
            f"{count} — a double-seed would produce a multiple of it"
        )
    finally:
        await provider.clear(agent_id, session_id=session_id)
        client = provider._require_client()  # type: ignore[attr-defined]
        await client.delete(provider._seed_lock_key(agent_id, session_id))  # type: ignore[attr-defined]
        await provider.disconnect()
