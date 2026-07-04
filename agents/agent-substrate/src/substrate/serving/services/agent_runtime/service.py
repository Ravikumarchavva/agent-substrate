"""Agent Runtime service logic."""

from __future__ import annotations

from substrate.logger import setup_logging

import httpx

from substrate.infrastructure.serving_factory import build_agent_for_run
from substrate.integrations.events import EventBus
from substrate.integrations.events.envelope import EventEnvelope
from substrate.kernel.core.content import ChatMessage, Role
from substrate.kernel.core.identity import AgentId
from substrate.kernel.messaging.message import ChatPayload, Message
from substrate.kernel.storage.history import HistoryProvider
from substrate.kernel import TextBlock

logger = setup_logging()


async def load_memory_for_thread(
    *,
    thread_id: str,
    system_instructions: str,
    history: HistoryProvider | None,
    conversation_service_url: str,
) -> object:
    """Load agent history from the cache or the conversation service."""
    from substrate.agents.factory import load_session_memory

    async def _load_persisted_steps() -> list[dict]:
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
    model_client: object,
    tools: list,
    system_instructions: str,
    memory: object,
    session_id: str | None = None,
    model_context_window: int = 40,
    max_iterations: int = 30,
) -> object:
    """Create the agent used by the runtime service."""
    return build_agent_for_run(
        model_client=model_client,
        tools=tools,
        system_instructions=system_instructions,
        memory=memory,
        session_id=session_id,
        model_context_window=model_context_window,
        max_iterations=max_iterations,
    )


async def execute_agent_run(
    *,
    agent: object,
    user_content: str,
    run_id: str,
    thread_id: str,
    event_bus: EventBus,
    runtime: object,
) -> None:
    """Execute an agent run and publish distributed runtime events via the EventBus."""
    await runtime.register(agent)  # type: ignore[union-attr]

    msg = Message(
        target=agent.id,  # type: ignore[union-attr]
        sender=AgentId(type="proxy", key="job"),
        payload=ChatPayload(
            message=ChatMessage(role=Role.USER, content=[TextBlock(text=user_content)])
        ),
        correlation_id=thread_id,
    )
    actual_run_id = await runtime.submit(agent.id, msg)  # type: ignore[union-attr]

    async for entry in runtime.event_log.tail(actual_run_id):  # type: ignore[union-attr]
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
