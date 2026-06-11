from __future__ import annotations

import pytest
import redis.exceptions
from ravi.capabilities.history import RedisHistoryProvider
from ravi.kernel import AgentId
from ravi.kernel.content import ChatMessage, TextBlock


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
