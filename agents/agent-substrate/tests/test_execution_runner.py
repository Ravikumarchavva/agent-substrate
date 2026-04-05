"""Tests for shared execution streaming helpers."""

from __future__ import annotations

from typing import Any, AsyncIterator
from unittest.mock import AsyncMock

from raavan.core.execution.context import ExecutionContext
from raavan.core.messages import CompletionChunk, ReasoningDeltaChunk, TextDeltaChunk
from raavan.core.messages.client_messages import (
    AssistantMessage,
    ToolExecutionResultMessage,
)
from raavan.services.agent_runtime.service import execute_agent_run
from raavan.shared.execution.runner import stream_agent_run


class FakeStreamingAgent:
    def __init__(self, chunks: list[Any], *, error: Exception | None = None) -> None:
        self.execution_context: ExecutionContext | None = ExecutionContext(
            run_id="previous-run",
            correlation_id="previous-run",
            metadata={"source": "previous"},
        )
        self._chunks = chunks
        self._error = error
        self.observed_run_ids: list[str] = []

    async def run_stream(self, user_content: str) -> AsyncIterator[Any]:
        assert self.execution_context is not None
        self.observed_run_ids.append(self.execution_context.run_id)
        for chunk in self._chunks:
            yield chunk
        if self._error is not None:
            raise self._error


async def test_stream_agent_run_dispatches_callbacks_and_restores_context() -> None:
    completion = CompletionChunk(
        AssistantMessage(role="assistant", content=["done"], finish_reason="stop")
    )
    tool_result = ToolExecutionResultMessage(
        tool_call_id="call-1",
        name="search_docs",
        content=[{"type": "text", "text": "ok"}],
    )
    agent = FakeStreamingAgent(
        [
            TextDeltaChunk("he"),
            ReasoningDeltaChunk("thinking"),
            tool_result,
            completion,
            {"other": True},
        ]
    )

    seen: list[str] = []
    new_context = ExecutionContext(
        run_id="run-123",
        correlation_id="corr-123",
        thread_id="thread-1",
        input_text="hello",
    )

    step_count = await stream_agent_run(
        agent=agent,
        user_content="hello",
        execution_context=new_context,
        on_text_delta=lambda chunk: _append(seen, f"text:{chunk.text}"),
        on_reasoning_delta=lambda chunk: _append(seen, f"reasoning:{chunk.text}"),
        on_tool_result=lambda chunk: _append(seen, f"tool:{chunk.name}"),
        on_completion=lambda message: _append(seen, f"completion:{message.content[0]}"),
        on_unknown=lambda chunk: _append(seen, f"unknown:{chunk['other']}"),
    )

    assert step_count == 2
    assert seen == [
        "text:he",
        "reasoning:thinking",
        "tool:search_docs",
        "completion:done",
        "unknown:True",
    ]
    assert agent.observed_run_ids == ["run-123"]
    assert agent.execution_context is not None
    assert agent.execution_context.run_id == "previous-run"


async def test_execute_agent_run_publishes_completion_events() -> None:
    agent = FakeStreamingAgent(
        [
            TextDeltaChunk("hello"),
            ReasoningDeltaChunk("plan"),
            ToolExecutionResultMessage(
                tool_call_id="call-1",
                name="search_docs",
                content=[{"type": "text", "text": "found it"}],
            ),
            CompletionChunk(
                AssistantMessage(
                    role="assistant",
                    content=["final answer"],
                    finish_reason="stop",
                )
            ),
        ]
    )
    event_bus = AsyncMock()

    await execute_agent_run(
        agent=agent,
        user_content="hello",
        run_id="run-abc",
        thread_id="thread-abc",
        event_bus=event_bus,
    )

    published = [call.args[0] for call in event_bus.publish.await_args_list]
    assert [event.event_type for event in published] == [
        "agent.text_delta",
        "agent.reasoning_delta",
        "agent.tool_result",
        "agent.completion",
        "agent.run_completed",
    ]
    assert published[-1].payload["steps_count"] == 2
    assert published[-1].payload["run_id"] == "run-abc"


async def test_execute_agent_run_publishes_failure_event() -> None:
    agent = FakeStreamingAgent([], error=RuntimeError("boom"))
    event_bus = AsyncMock()

    await execute_agent_run(
        agent=agent,
        user_content="hello",
        run_id="run-fail",
        thread_id="thread-fail",
        event_bus=event_bus,
    )

    published = [call.args[0] for call in event_bus.publish.await_args_list]
    assert [event.event_type for event in published] == ["agent.run_failed"]
    assert published[0].payload["error"] == "boom"


async def _append(items: list[str], value: str) -> None:
    items.append(value)
