"""Agent Runtime service logic."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from raavan.core.agents.react_agent import ReActAgent
from raavan.core.context.implementations import SlidingWindowContext
from raavan.core.execution.context import ExecutionContext
from raavan.core.memory.base_memory import BaseMemory
from raavan.core.messages.client_messages import (
    AssistantMessage,
    ToolExecutionResultMessage,
)
from raavan.core.llm.base_client import BaseModelClient
from raavan.core.tools.base_tool import BaseTool
from raavan.integrations.memory.redis_memory import RedisMemory
from raavan.shared.events.bus import EventBus
from raavan.shared.events.envelope import EventEnvelope
from raavan.shared.execution import (
    create_react_agent,
    load_session_memory,
    stream_agent_run,
)

logger = logging.getLogger(__name__)


async def load_memory_for_thread(
    *,
    thread_id: str,
    system_instructions: str,
    redis_memory: Optional[RedisMemory],
    conversation_service_url: str,
) -> BaseMemory:
    """Load agent memory from Redis or the conversation service."""

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
        redis_memory=redis_memory,
        cold_store_name="Conversation service",
        load_persisted_steps=_load_persisted_steps,
    )


def create_agent(
    *,
    model_client: BaseModelClient,
    tools: List[BaseTool],
    system_instructions: str,
    memory: BaseMemory,
    model_context_window: int = 40,
    max_iterations: int = 30,
) -> ReActAgent:
    """Create the agent used by the runtime service."""
    return create_react_agent(
        model_client=model_client,
        tools=tools,
        system_instructions=system_instructions,
        memory=memory,
        model_context=SlidingWindowContext(max_messages=model_context_window),
        max_iterations=max_iterations,
        verbose=True,
    )


def _serialize_completion_content(message: AssistantMessage) -> list[str] | None:
    if not message.content:
        return None
    texts = [item for item in message.content if isinstance(item, str)]
    return ["\n".join(texts)] if texts else None


async def execute_agent_run(
    *,
    agent: ReActAgent,
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

    async def _publish_completion(message: AssistantMessage) -> None:
        tool_calls = None
        if message.tool_calls:
            tool_calls = [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in message.tool_calls
            ]

        await event_bus.publish(
            EventEnvelope(
                event_type="agent.completion",
                correlation_id=run_id,
                payload={
                    "type": "completion",
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "content": _serialize_completion_content(message),
                    "tool_calls": tool_calls,
                    "finish_reason": message.finish_reason,
                    "has_tool_calls": bool(message.tool_calls),
                    "partial": False,
                    "complete": True,
                },
            )
        )

    async def _publish_tool_result(chunk: ToolExecutionResultMessage) -> None:
        content_text = ""
        if isinstance(chunk.content, list):
            parts = []
            for block in chunk.content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            content_text = "\n".join(parts)

        await event_bus.publish(
            EventEnvelope(
                event_type="agent.tool_result",
                correlation_id=run_id,
                payload={
                    "type": "tool_result",
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "tool_name": getattr(chunk, "name", "unknown"),
                    "tool_call_id": getattr(chunk, "tool_call_id", ""),
                    "content": content_text,
                    "is_error": getattr(chunk, "is_error", False),
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
