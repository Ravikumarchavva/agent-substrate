from __future__ import annotations

import pytest
from ravi.agents.context import InMemoryHistoryProvider
from ravi.kernel import AgentId
from ravi.kernel.core.content import ChatMessage, TextBlock


@pytest.mark.asyncio
async def test_history_provider_contract():
    provider = InMemoryHistoryProvider()
    agent_id = AgentId(type="test", key="agent_123")
    session_id = "session-abc"

    msgs = await provider.get_messages(agent_id, session_id=session_id)
    assert msgs == []

    chat_msg = ChatMessage(role="user", content=[TextBlock(text="hello")])
    await provider.append(agent_id, chat_msg, session_id=session_id, run_id="run-1")

    msgs = await provider.get_messages(agent_id, session_id=session_id)
    assert len(msgs) == 1
    assert msgs[0].role == "user"
    assert msgs[0].metadata["run_id"] == "run-1"

    msgs_other = await provider.get_messages(agent_id, session_id="session-other")
    assert msgs_other == []

    await provider.clear(agent_id, session_id=session_id)
    msgs = await provider.get_messages(agent_id, session_id=session_id)
    assert msgs == []


@pytest.mark.asyncio
async def test_clear_run_scope():
    provider = InMemoryHistoryProvider()
    agent_id = AgentId(type="test", key="agent_run")
    session_id = "sess"

    m1 = ChatMessage(role="user", content=[TextBlock(text="run1")])
    m2 = ChatMessage(role="user", content=[TextBlock(text="run2")])
    await provider.append(agent_id, m1, session_id=session_id, run_id="run-a")
    await provider.append(agent_id, m2, session_id=session_id, run_id="run-b")

    await provider.clear_run(agent_id, session_id=session_id, run_id="run-a")
    remaining = await provider.get_messages(agent_id, session_id=session_id)
    assert len(remaining) == 1
    assert remaining[0].content[0].text == "run2"
