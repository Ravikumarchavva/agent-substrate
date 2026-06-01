from __future__ import annotations

import pytest
from ravi.agents.context import InMemoryHistoryProvider
from ravi.kernel.content import ChatMessage, TextBlock


@pytest.mark.asyncio
async def test_history_provider_contract():
    provider = InMemoryHistoryProvider()
    agent_id = "agent_123"
    
    # Check initially empty
    msgs = await provider.get_messages(agent_id)
    assert msgs == []

    # Append message
    msg = ChatMessage(role="user", content=[TextBlock(text="hello")])
    await provider.append(agent_id, msg)
    
    # Get messages
    msgs = await provider.get_messages(agent_id)
    assert len(msgs) == 1
    assert msgs[0].role == "user"
    assert msgs[0].content[0].text == "hello"

    # Clear history
    await provider.clear(agent_id)
    msgs = await provider.get_messages(agent_id)
    assert msgs == []
