from __future__ import annotations

import pytest
import redis.exceptions
from ravi.capabilities.history import RedisHistoryProvider
from ravi.kernel import AgentId, Message, ChatMessage
from ravi.kernel.content import TextBlock


@pytest.mark.asyncio
async def test_redis_history_provider():
    provider = RedisHistoryProvider(redis_url="redis://localhost:6379/0", ttl=60)
    try:
        await provider.connect()
    except (redis.exceptions.ConnectionError, OSError) as e:
        pytest.skip(f"Redis is not available: {e}")

    try:
        agent_id = AgentId(type="user", key="123")
        run_id = "test-session-001"

        # Ensure clean state
        await provider.clear(agent_id, session_id=run_id)
        assert await provider.count_messages(agent_id, session_id=run_id) == 0

        # Append a message envelope
        msg = Message(
            target=agent_id,
            sender=None,
            payload=ChatMessage(role="user", content=[TextBlock(text="redis message")]),
        )
        await provider.append(agent_id, msg, session_id=run_id)

        # Assertions
        assert await provider.count_messages(agent_id, session_id=run_id) == 1
        msgs = await provider.get_messages(agent_id, session_id=run_id)
        assert len(msgs) == 1
        assert msgs[0].target.type == "user"
        assert msgs[0].target.key == "123"
        assert isinstance(msgs[0].payload, ChatMessage)
        assert msgs[0].payload.content[0].text == "redis message"

        # Cleanup
        await provider.clear(agent_id, session_id=run_id)
        assert await provider.count_messages(agent_id, session_id=run_id) == 0
    finally:
        await provider.disconnect()
