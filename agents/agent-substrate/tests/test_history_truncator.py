from __future__ import annotations

import pytest

from ravi.core.middleware.base import MiddlewareContext, MiddlewareStage
from ravi.core.middleware.builtins.history_truncator import HistoryTruncatorMiddleware
from ravi.core.messages.client_messages import (
    SystemMessage,
    UserMessage,
    AssistantMessage,
)


@pytest.mark.asyncio
async def test_history_truncator_middleware():
    truncator = HistoryTruncatorMiddleware(max_messages=5)

    system_msg = SystemMessage(content="You are a helpful assistant.")
    messages = [
        system_msg,
        UserMessage(content=["Hello 1"]),
        AssistantMessage(content=["Hi 1"]),
        UserMessage(content=["Hello 2"]),
        AssistantMessage(content=["Hi 2"]),
        UserMessage(content=["Hello 3"]),
        AssistantMessage(content=["Hi 3"]),
        UserMessage(content=["Hello 4"]),
    ]

    ctx = MiddlewareContext(
        stage=MiddlewareStage.LLM_CALL, metadata={"messages": list(messages)}
    )

    # Before execution
    ctx = await truncator.before(ctx)
    pruned = ctx.metadata["messages"]

    # We asked for max 5 messages. SystemMessage must be preserved.
    # The last 4 messages should be preserved (allowing total of 5 including SystemMessage).
    assert len(pruned) == 5
    assert pruned[0] == system_msg
    assert pruned[1].content[0] == "Hi 2"
    assert pruned[2].content[0] == "Hello 3"
    assert pruned[3].content[0] == "Hi 3"
    assert pruned[4].content[0] == "Hello 4"

    # Verify metadata fields populated by truncator
    assert ctx.metadata["_original_message_count"] == 8
    assert ctx.metadata["_pruned_message_count"] == 5
