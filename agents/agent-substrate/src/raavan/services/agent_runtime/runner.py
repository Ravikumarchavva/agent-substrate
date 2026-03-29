"""Agent Runtime — agent creation and execution.

Wraps the existing ReActAgent + RedisMemory + SlidingWindowContext
in a service-aware runner that:
1. Receives run commands from the Workflow Orchestrator (via event bus)
2. Loads agent memory from Redis (hot) / Conversation service (cold)
3. Streams agent output as events for the Stream Projection service
4. Publishes completion/failure events back to the Workflow Orchestrator
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from raavan.core.agents.react_agent import ReActAgent
from raavan.core.context.implementations import SlidingWindowContext
from raavan.core.guardrails.prebuilt import MaxTokenGuardrail
from raavan.core.memory.redis_memory import RedisMemory
from raavan.core.memory.unbounded_memory import UnboundedMemory
from raavan.core.memory.base_memory import BaseMemory
from raavan.core.messages import (
    CompletionChunk,
    ReasoningDeltaChunk,
    TextDeltaChunk,
)
from raavan.core.messages.client_messages import (
    AssistantMessage,
    SystemMessage,
    ToolExecutionResultMessage,
    UserMessage,
)
from raavan.core.messages.base_message import BaseClientMessage
from raavan.core.tools.base_tool import BaseTool
from raavan.integrations.llm.base_client import BaseModelClient
from raavan.shared.events.bus import EventBus
from raavan.shared.events.envelope import EventEnvelope

logger = logging.getLogger(__name__)


async def _rebuild_messages(
    step_rows: List[Dict[str, Any]],
    system_instructions: str,
) -> List[BaseClientMessage]:
    """Rebuild message list from Conversation service step rows."""
    messages: List[BaseClientMessage] = [SystemMessage(content=system_instructions)]

    for row in step_rows:
        step_type = row["type"]
        meta = row.get("metadata") or {}

        if step_type == "system_message":
            continue
        elif step_type == "user_message":
            messages.append(UserMessage(content=[row.get("input") or ""]))
        elif step_type == "assistant_message":
            output_text = row.get("output")
            content = [output_text] if output_text else None
            messages.append(
                AssistantMessage(
                    content=content,
                    finish_reason="stop",
                )
            )
        elif step_type == "tool_result":
            messages.append(
                ToolExecutionResultMessage(
                    tool_call_id=meta.get("tool_call_id", ""),
                    name=row.get("name", ""),
                    content=[{"type": "text", "text": row.get("output") or ""}],
                    is_error=row.get("is_error") or False,
                )
            )

    return messages


async def load_memory_for_thread(
    *,
    thread_id: str,
    system_instructions: str,
    redis_memory: Optional[RedisMemory],
    conversation_service_url: str,
) -> BaseMemory:
    """Load agent memory from Redis (hot) or Conversation service (cold).

    Returns a per-request memory instance ready for agent use.
    """
    if redis_memory is not None:
        per_request_mem = RedisMemory.for_session(redis_memory, thread_id)
        in_redis = await redis_memory.exists(thread_id)

        if in_redis:
            count = await per_request_mem.restore()
            logger.debug("Redis hit for %s — %d messages", thread_id, count)
            return per_request_mem

        # Cold path: fetch from Conversation service
        logger.debug(
            "Redis miss for %s — fetching from Conversation service", thread_id
        )
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{conversation_service_url}/internal/threads/{thread_id}/memory"
            )
            resp.raise_for_status()
            step_rows = resp.json()

        all_messages = await _rebuild_messages(step_rows, system_instructions)
        if all_messages:
            await redis_memory.store_many(thread_id, all_messages)
        await per_request_mem.restore()
        return per_request_mem

    # Fallback: no Redis
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{conversation_service_url}/internal/threads/{thread_id}/memory"
        )
        resp.raise_for_status()
        step_rows = resp.json()

    all_messages = await _rebuild_messages(step_rows, system_instructions)
    fallback_mem = UnboundedMemory()
    for msg in all_messages:
        await fallback_mem.add_message(msg)
    return fallback_mem


def create_agent(
    *,
    model_client: BaseModelClient,
    tools: List[BaseTool],
    system_instructions: str,
    memory: BaseMemory,
    model_context_window: int = 40,
    max_iterations: int = 30,
    tool_approval_handler=None,
    tools_requiring_approval: Optional[List[str]] = None,
    tool_timeout: Optional[float] = None,
) -> ReActAgent:
    """Create a configured ReActAgent."""
    context = SlidingWindowContext(max_messages=model_context_window)

    kwargs: Dict[str, Any] = dict(
        name="ChatBot",
        description="A helpful AI assistant with tool access.",
        model_client=model_client,
        model_context=context,
        tools=tools,
        system_instructions=system_instructions,
        memory=memory,
        max_iterations=max_iterations,
        verbose=True,
        input_guardrails=[
            MaxTokenGuardrail(max_tokens=16_000, model="gpt-4o", tripwire=True)
        ],
    )
    if tool_approval_handler is not None:
        kwargs["tool_approval_handler"] = tool_approval_handler
    if tools_requiring_approval is not None:
        kwargs["tools_requiring_approval"] = tools_requiring_approval
    if tool_timeout is not None:
        kwargs["tool_timeout"] = tool_timeout

    return ReActAgent(**kwargs)


async def run_agent_stream(
    *,
    agent: ReActAgent,
    user_content: str,
    run_id: str,
    thread_id: str,
    event_bus: EventBus,
) -> None:
    """Execute the agent's ReAct loop, publishing events to the event bus.

    Events published:
    - agent.text_delta     — streaming text tokens
    - agent.reasoning_delta — reasoning tokens
    - agent.completion     — full assistant message
    - agent.tool_result    — tool execution result
    - agent.run_completed  — final completion signal
    - agent.run_failed     — error signal
    """
    try:
        step_count = 0
        async for chunk in agent.run_stream(user_content):
            if isinstance(chunk, TextDeltaChunk):
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

            elif isinstance(chunk, ReasoningDeltaChunk):
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

            elif isinstance(chunk, CompletionChunk):
                step_count += 1
                msg = chunk.message
                content_text = None
                if msg.content:
                    texts = [c for c in msg.content if isinstance(c, str)]
                    content_text = "\n".join(texts) if texts else None

                tool_calls = None
                if msg.tool_calls:
                    tool_calls = [
                        {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                        for tc in msg.tool_calls
                    ]

                await event_bus.publish(
                    EventEnvelope(
                        event_type="agent.completion",
                        correlation_id=run_id,
                        payload={
                            "type": "completion",
                            "run_id": run_id,
                            "thread_id": thread_id,
                            "content": [content_text] if content_text else None,
                            "tool_calls": tool_calls,
                            "finish_reason": msg.finish_reason,
                            "has_tool_calls": bool(msg.tool_calls),
                            "partial": False,
                            "complete": True,
                        },
                    )
                )

            elif isinstance(chunk, ToolExecutionResultMessage):
                step_count += 1
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

        # Run completed successfully
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

    except Exception as exc:
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
