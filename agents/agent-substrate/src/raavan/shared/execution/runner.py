"""Shared execution runner for streaming agent output."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from raavan.core.execution.context import ExecutionContext
from raavan.core.messages import CompletionChunk, ReasoningDeltaChunk, TextDeltaChunk
from raavan.core.messages.client_messages import (
    AssistantMessage,
    ToolExecutionResultMessage,
)

TextDeltaHandler = Callable[[TextDeltaChunk], Awaitable[None]]
ReasoningDeltaHandler = Callable[[ReasoningDeltaChunk], Awaitable[None]]
CompletionHandler = Callable[[AssistantMessage], Awaitable[None]]
ToolResultHandler = Callable[[ToolExecutionResultMessage], Awaitable[None]]
UnknownChunkHandler = Callable[[Any], Awaitable[None]]
FinishedHandler = Callable[[int], Awaitable[None]]
ErrorHandler = Callable[[Exception], Awaitable[None]]


async def stream_agent_run(
    *,
    agent: Any,
    user_content: str,
    execution_context: Optional[ExecutionContext] = None,
    on_text_delta: Optional[TextDeltaHandler] = None,
    on_reasoning_delta: Optional[ReasoningDeltaHandler] = None,
    on_completion: Optional[CompletionHandler] = None,
    on_tool_result: Optional[ToolResultHandler] = None,
    on_unknown: Optional[UnknownChunkHandler] = None,
    on_finished: Optional[FinishedHandler] = None,
    on_error: Optional[ErrorHandler] = None,
) -> int:
    """Run ``agent.run_stream()`` and dispatch chunks via callbacks."""
    step_count = 0
    previous_context = getattr(agent, "execution_context", None)
    if execution_context is not None and hasattr(agent, "execution_context"):
        agent.execution_context = execution_context

    try:
        async for chunk in agent.run_stream(user_content):
            if isinstance(chunk, TextDeltaChunk):
                if on_text_delta is not None:
                    await on_text_delta(chunk)
                continue

            if isinstance(chunk, ReasoningDeltaChunk):
                if on_reasoning_delta is not None:
                    await on_reasoning_delta(chunk)
                continue

            if isinstance(chunk, CompletionChunk):
                step_count += 1
                if on_completion is not None:
                    await on_completion(chunk.message)
                continue

            if isinstance(chunk, ToolExecutionResultMessage):
                step_count += 1
                if on_tool_result is not None:
                    await on_tool_result(chunk)
                continue

            if on_unknown is not None:
                await on_unknown(chunk)

        if on_finished is not None:
            await on_finished(step_count)
        return step_count

    except Exception as exc:
        if on_error is None:
            raise
        await on_error(exc)
        return step_count

    finally:
        if hasattr(agent, "execution_context"):
            agent.execution_context = previous_context
