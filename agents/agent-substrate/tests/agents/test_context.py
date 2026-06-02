from __future__ import annotations

import pytest
from ravi.agents.context import (
    AgentContext,
    DefaultAgentContext,
    InMemoryHistoryProvider,
    SlidingWindowCompaction,
)
from ravi.kernel import AgentId
from ravi.kernel.content import ChatMessage, TextBlock
from ravi.kernel.message import Message


@pytest.mark.asyncio
async def test_sliding_window_compaction():
    strategy = SlidingWindowCompaction(max_messages=2)
    agent_id = AgentId(type="test", key="a1")
    messages = [
        Message(target=agent_id, payload=ChatMessage(role="user", content=[TextBlock(text="1")]), sender=agent_id),
        Message(target=agent_id, payload=ChatMessage(role="user", content=[TextBlock(text="2")]), sender=agent_id),
        Message(target=agent_id, payload=ChatMessage(role="user", content=[TextBlock(text="3")]), sender=agent_id),
    ]
    compacted = await strategy.compact(messages)
    assert len(compacted) == 2
    assert compacted[0].payload.content[0].text == "2"
    assert compacted[1].payload.content[0].text == "3"


@pytest.mark.asyncio
async def test_agent_context():
    # Constructor
    history = InMemoryHistoryProvider()
    compaction = SlidingWindowCompaction(max_messages=10)
    ctx = AgentContext(history, compaction)

    assert ctx.history is history
    assert ctx.compaction is compaction

    # Default constructor
    default_ctx = AgentContext.default()
    assert isinstance(default_ctx.history, InMemoryHistoryProvider)
    assert isinstance(default_ctx.compaction, SlidingWindowCompaction)


@pytest.mark.asyncio
async def test_default_agent_context():
    history = InMemoryHistoryProvider()
    compaction = SlidingWindowCompaction(max_messages=10)
    agent_id = AgentId(type="assistant", key="agent_1")
    session_id = "test-session"

    # Append message
    chat_msg = ChatMessage(role="user", content=[TextBlock(text="hi")])
    envelope = Message(target=agent_id, payload=chat_msg, sender=agent_id)
    await history.append(agent_id, envelope, session_id=session_id)

    default_ctx = DefaultAgentContext(agent_id, history, compaction)
    assert default_ctx.agent_id == agent_id
    assert default_ctx.history is history
    assert default_ctx.compaction is compaction

    # Test prompt window retrieval uses session_id
    window = await default_ctx.get_prompt_window(session_id)
    assert len(window) == 1
    assert window[0].payload.content[0].text == "hi"

    # Different session_id yields empty window
    window_other = await default_ctx.get_prompt_window("other-session")
    assert len(window_other) == 0
