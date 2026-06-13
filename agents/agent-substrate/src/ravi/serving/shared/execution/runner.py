"""Shared execution runner for streaming agent output."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from ravi.kernel.messaging.stream import (
    AgentProgress,
    CompletionEvent,
    ReasoningDelta,
    StreamDone,
    TextDelta,
)

TextDeltaHandler = Callable[[TextDelta], Awaitable[None]]
ReasoningDeltaHandler = Callable[[ReasoningDelta], Awaitable[None]]
CompletionHandler = Callable[[CompletionEvent], Awaitable[None]]
AgentProgressHandler = Callable[[AgentProgress], Awaitable[None]]
UnknownChunkHandler = Callable[[Any], Awaitable[None]]
FinishedHandler = Callable[[int], Awaitable[None]]
ErrorHandler = Callable[[Exception], Awaitable[None]]


async def stream_agent_run(
    *,
    agent: Any,
    user_content: str,
    execution_context: Any = None,
    on_text_delta: Optional[TextDeltaHandler] = None,
    on_reasoning_delta: Optional[ReasoningDeltaHandler] = None,
    on_completion: Optional[CompletionHandler] = None,
    on_agent_progress: Optional[AgentProgressHandler] = None,
    on_tool_result: Any = None,  # kept for call-site compat; no longer dispatched
    on_unknown: Optional[UnknownChunkHandler] = None,
    on_finished: Optional[FinishedHandler] = None,
    on_error: Optional[ErrorHandler] = None,
    **agent_run_kwargs: Any,
) -> int:
    """Run ``agent.run_stream()`` and dispatch events via callbacks.

    Dispatches:
    - ``TextDelta``        → ``on_text_delta``
    - ``ReasoningDelta``   → ``on_reasoning_delta``
    - ``CompletionEvent``  → ``on_completion``
    - ``AgentProgress``    → ``on_agent_progress``
    - ``StreamDone``       → loop exits; ``on_finished(step_count)`` called
    - anything else        → ``on_unknown``
    """
    step_count = 0
    try:
        async for chunk in agent.run_stream(user_content, **agent_run_kwargs):
            if isinstance(chunk, TextDelta):
                if on_text_delta is not None:
                    await on_text_delta(chunk)
                continue

            if isinstance(chunk, ReasoningDelta):
                if on_reasoning_delta is not None:
                    await on_reasoning_delta(chunk)
                continue

            if isinstance(chunk, CompletionEvent):
                step_count += 1
                if on_completion is not None:
                    await on_completion(chunk)
                continue

            if isinstance(chunk, AgentProgress):
                if on_agent_progress is not None:
                    await on_agent_progress(chunk)
                continue

            if isinstance(chunk, StreamDone):
                break

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
