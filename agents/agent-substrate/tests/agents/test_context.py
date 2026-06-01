from __future__ import annotations

import pytest
from ravi.agents.context import (
    AgentContext,
    DefaultAgentContext,
    InMemoryHistoryProvider,
    SlidingWindowCompaction,
)
from ravi.kernel.content import ChatMessage, TextBlock


@pytest.mark.asyncio
async def test_sliding_window_compaction():
    strategy = SlidingWindowCompaction(max_messages=2)
    messages = [
        ChatMessage(role="user", content=[TextBlock(text="1")]),
        ChatMessage(role="user", content=[TextBlock(text="2")]),
        ChatMessage(role="user", content=[TextBlock(text="3")]),
    ]
    compacted = await strategy.compact(messages)
    assert len(compacted) == 2
    assert compacted[0].content[0].text == "2"
    assert compacted[1].content[0].text == "3"


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
    
    # Append message
    msg = ChatMessage(role="user", content=[TextBlock(text="hi")])
    await history.append("agent_1", msg)

    default_ctx = DefaultAgentContext("agent_1", history, compaction)
    assert default_ctx.agent_id == "agent_1"
    assert default_ctx.history is history
    assert default_ctx.compaction is compaction

    # Test prompt window retrieval
    window = await default_ctx.get_prompt_window()
    assert len(window) == 1
    assert window[0].content[0].text == "hi"
