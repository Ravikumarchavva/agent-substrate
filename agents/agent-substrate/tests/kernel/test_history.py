from __future__ import annotations

import pytest
from ravi.agents.context import InMemoryHistoryProvider
from ravi.kernel import AgentId
from ravi.kernel.content import ChatMessage, TextBlock
from ravi.kernel.message import Message


@pytest.mark.asyncio
async def test_history_provider_contract():
    provider = InMemoryHistoryProvider()
    agent_id = AgentId(type="test", key="agent_123")
    session_id = "session-abc"

    # Check initially empty
    msgs = await provider.get_messages(agent_id, session_id=session_id)
    assert msgs == []

    # Append message
    chat_msg = ChatMessage(role="user", content=[TextBlock(text="hello")])
    envelope = Message(target=agent_id, payload=chat_msg, sender=agent_id)
    await provider.append(agent_id, envelope, session_id=session_id)

    # Get messages
    msgs = await provider.get_messages(agent_id, session_id=session_id)
    assert len(msgs) == 1
    assert isinstance(msgs[0].payload, ChatMessage)
    assert msgs[0].payload.role == "user"

    # Different session_id — should be isolated
    msgs_other = await provider.get_messages(agent_id, session_id="session-other")
    assert msgs_other == []

    # Clear history for this session only
    await provider.clear(agent_id, session_id=session_id)
    msgs = await provider.get_messages(agent_id, session_id=session_id)
    assert msgs == []
