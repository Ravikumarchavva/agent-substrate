"""Agent Runtime service logic."""

from __future__ import annotations
from ravi.logger import setup_logging

from typing import Any, Dict, List, Optional

import httpx

from ravi.reasoning.agents.assistant.agent import AssistantAgent
from ravi.fabric.context import (
    HistoryProvider,
    SlidingWindowCompaction as SlidingWindowStrategy,
)
from ravi.kernel import (
    ChatMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    Tool as BaseTool,
)
from ravi.fabric.llm import LLMClient as BaseModelClient
from ravi.integrations.events import EventBus
from ravi.shared.events.envelope import EventEnvelope
from ravi.shared.execution import (
    create_assistant_agent,
    load_session_memory,
    stream_agent_run,
)

logger = setup_logging()


class ExecutionContext:
    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


async def load_memory_for_thread(
    *,
    thread_id: str,
    system_instructions: str,
    history: Optional[HistoryProvider],
    conversation_service_url: str,
) -> HistoryProvider:
    """Load agent history from the cache or the conversation service."""

    async def _load_persisted_steps() -> List[Dict[str, Any]]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{conversation_service_url}/internal/threads/{thread_id}/memory"
            )
            response.raise_for_status()
            return response.json()

    return await load_session_memory(
        session_id=thread_id,
        system_instructions=system_instructions,
        history=history,
        cold_store_name="Conversation service",
        load_persisted_steps=_load_persisted_steps,
    )


def create_agent(
    *,
    model_client: BaseModelClient,
    tools: List[BaseTool],
    system_instructions: str,
    memory: HistoryProvider,
    session_id: Optional[str] = None,
    model_context_window: int = 40,
    max_iterations: int = 30,
) -> AssistantAgent:
    """Create the agent used by the runtime service."""
    return create_assistant_agent(
        model_client=model_client,
        tools=tools,
        system_instructions=system_instructions,
        memory=memory,
        session_id=session_id,
        model_context=SlidingWindowStrategy(max_messages=model_context_window),
        max_iterations=max_iterations,
        verbose=True,
    )


def _serialize_completion_content(message: ChatMessage) -> list[str] | None:
    if not message.content:
        return None
    texts = []
    for item in message.content:
        if isinstance(item, TextBlock):
            texts.append(item.text)
        elif isinstance(item, str):
            texts.append(item)
    return ["\n".join(texts)] if texts else None


async def execute_agent_run(
    *,
    agent: AssistantAgent,
    user_content: str,
    run_id: str,
    thread_id: str,
    event_bus: EventBus,
) -> None:
    """Execute a streaming agent run and publish distributed runtime events."""
    failed = False

    async def _publish_text_delta(chunk) -> None:
        await event_bus.publish(
            EventEnvelope(
                event_type="agent.text_delta",
                correlation_id=run_id,
                payload={
                    "type": "text_delta",
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "content": chunk.text,
                    "partial": True,
                },
            )
        )

    async def _publish_reasoning_delta(chunk) -> None:
        await event_bus.publish(
            EventEnvelope(
                event_type="agent.reasoning_delta",
                correlation_id=run_id,
                payload={
                    "type": "reasoning_delta",
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "content": chunk.text,
                    "partial": True,
                },
            )
        )

    async def _publish_completion(message: ChatMessage) -> None:
        tool_calls = []
        for block in message.content:
            if isinstance(block, ToolUseBlock):
                tool_calls.append({
                    "id": block.call_id,
                    "name": block.tool_name,
                    "arguments": block.arguments,
                })

        await event_bus.publish(
            EventEnvelope(
                event_type="agent.completion",
                correlation_id=run_id,
                payload={
                    "type": "completion",
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "content": _serialize_completion_content(message),
                    "tool_calls": tool_calls or None,
                    "finish_reason": "stop",
                    "has_tool_calls": bool(tool_calls),
                    "partial": False,
                    "complete": True,
                },
            )
        )

    async def _publish_tool_result(chunk: ChatMessage) -> None:
        content_text = ""
        tool_name = "unknown"
        call_id = ""
        is_error = False

        for block in chunk.content:
            if isinstance(block, ToolResultBlock):
                tool_name = block.tool_name
                call_id = block.call_id
                is_error = block.is_error
                if block.content:
                    parts = [
                        sub_block.text
                        for sub_block in block.content
                        if isinstance(sub_block, TextBlock)
                    ]
                    content_text = "\n".join(parts)

        await event_bus.publish(
            EventEnvelope(
                event_type="agent.tool_result",
                correlation_id=run_id,
                payload={
                    "type": "tool_result",
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "tool_name": tool_name,
                    "tool_call_id": call_id,
                    "content": content_text,
                    "is_error": is_error,
                    "partial": False,
                },
            )
        )

    async def _publish_failure(exc: Exception) -> None:
        nonlocal failed
        failed = True
        logger.exception("Agent run %s failed", run_id)
        await event_bus.publish(
            EventEnvelope(
                event_type="agent.run_failed",
                correlation_id=run_id,
                payload={
                    "type": "agent.run_failed",
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "error": str(exc),
                },
            )
        )

    step_count = await stream_agent_run(
        agent=agent,
        user_content=user_content,
        execution_context=ExecutionContext(
            run_id=run_id,
            correlation_id=run_id,
            thread_id=thread_id,
            input_text=user_content,
        ),
        on_text_delta=_publish_text_delta,
        on_reasoning_delta=_publish_reasoning_delta,
        on_completion=_publish_completion,
        on_tool_result=_publish_tool_result,
        on_error=_publish_failure,
    )

    if failed:
        return

    await event_bus.publish(
        EventEnvelope(
            event_type="agent.run_completed",
            correlation_id=run_id,
            payload={
                "type": "agent.run_completed",
                "run_id": run_id,
                "thread_id": thread_id,
                "steps_count": step_count,
            },
        )
    )
