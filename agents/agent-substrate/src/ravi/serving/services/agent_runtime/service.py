"""Agent Runtime service logic."""

from __future__ import annotations
from ravi.logger import setup_logging

from typing import Any, Dict, List, Optional

import httpx

from ravi.agents.core import ReActAgent
from ravi.agents.context import (
    HistoryProvider,
    SlidingWindowCompaction,
)
from ravi.kernel import (
    TextBlock,
    Tool as BaseTool,
)
from ravi.kernel.core.content import ChatMessage, Role
from ravi.kernel.core.identity import AgentId
from ravi.kernel.messaging.message import ChatPayload, Message
from ravi.kernel.llm import LLMClient
from ravi.integrations.events import EventBus
from ravi.integrations.events.envelope import EventEnvelope
from ravi.agents.factory import create_assistant_agent, load_session_memory

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
    model_client: LLMClient,
    tools: List[BaseTool],
    system_instructions: str,
    memory: HistoryProvider,
    session_id: Optional[str] = None,
    model_context_window: int = 40,
    max_iterations: int = 30,
    runtime: Any = None,
) -> ReActAgent:
    """Create the agent used by the runtime service."""
    return create_assistant_agent(
        runtime=runtime,
        model_client=model_client,
        tools=tools,
        system_instructions=system_instructions,
        memory=memory,
        model_context=SlidingWindowCompaction(max_messages=model_context_window),
        max_iterations=max_iterations,
    )


async def execute_agent_run(
    *,
    agent: ReActAgent,
    user_content: str,
    run_id: str,
    thread_id: str,
    event_bus: EventBus,
    runtime: Any,
) -> None:
    """Execute an agent run and publish distributed runtime events via the EventBus."""
    await runtime.register(agent)

    msg = Message(
        target=agent.id,
        sender=AgentId(type="proxy", key="job"),
        payload=ChatPayload(
            message=ChatMessage(role=Role.USER, content=[TextBlock(text=user_content)])
        ),
        correlation_id=thread_id,
    )
    actual_run_id = await runtime.submit(agent.id, msg)

    async for entry in runtime.event_log.tail(actual_run_id):
        kind = entry.kind
        p = entry.payload or {}

        if kind == "text.delta":
            await event_bus.publish(
                EventEnvelope(
                    event_type="agent.text_delta",
                    payload={
                        "type": "text_delta",
                        "run_id": run_id,
                        "thread_id": thread_id,
                        "content": p.get("text", ""),
                        "partial": True,
                    },
                )
            )

        elif kind == "reasoning.delta":
            await event_bus.publish(
                EventEnvelope(
                    event_type="agent.reasoning_delta",
                    payload={
                        "type": "reasoning_delta",
                        "run_id": run_id,
                        "thread_id": thread_id,
                        "content": p.get("text", ""),
                        "partial": True,
                    },
                )
            )

        elif kind == "tool.call":
            await event_bus.publish(
                EventEnvelope(
                    event_type="agent.tool_call",
                    payload={
                        "type": "tool_call",
                        "run_id": run_id,
                        "thread_id": thread_id,
                        "name": p.get("tool_name", ""),
                    },
                )
            )

        elif kind == "tool.result":
            await event_bus.publish(
                EventEnvelope(
                    event_type="agent.tool_result",
                    payload={
                        "type": "tool_result",
                        "run_id": run_id,
                        "thread_id": thread_id,
                        "name": p.get("tool_name", ""),
                        "ok": bool(p.get("ok", True)),
                    },
                )
            )

        elif kind == "run.completed":
            await event_bus.publish(
                EventEnvelope(
                    event_type="agent.run_completed",
                    correlation_id=run_id,
                    payload={
                        "type": "agent.run_completed",
                        "run_id": run_id,
                        "thread_id": thread_id,
                    },
                )
            )
            return

        elif kind == "run.failed":
            error = p.get("error", "Agent run failed")
            logger.error("Agent run %s failed: %s", run_id, error)
            await event_bus.publish(
                EventEnvelope(
                    event_type="agent.run_failed",
                    payload={
                        "type": "agent.run_failed",
                        "run_id": run_id,
                        "thread_id": thread_id,
                        "error": error,
                    },
                )
            )
            return

        elif kind == "run.cancelled":
            return
