"""Agent Runtime service logic."""

from __future__ import annotations

import logging

from substrate.types import RunLogKind

from substrate_cloud.factory import (
    build_agent_for_run,
    build_cached_history_for_thread,
)
from substrate.integrations.events import EventBus
from substrate.integrations.events.envelope import EventEnvelope
from substrate.types import ChatMessage, Role
from substrate.types import Actor
from substrate.runtime import ChatPayload, Message
from substrate.stores import ThreadStore
from substrate.types import TextBlock

logger = logging.getLogger(__name__)


async def load_memory_for_thread(
    *,
    thread_id: str,
    system_instructions: str,
    history: ThreadStore | None,
    conversation_service_url: str,
) -> object:
    """Wrap the shared history cache so it self-heals from the conversation
    service (the cold store for this microservice) on a cold session."""
    return await build_cached_history_for_thread(
        thread_id,
        system_instructions=system_instructions,
        history=history,
        conversation_service_url=conversation_service_url,
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
        sender=Actor(type="job_proxy"),
        payload=ChatPayload(
            message=ChatMessage(role=Role.USER, content=[TextBlock(text=user_content)])
        ),
        correlation_id=thread_id,
    )
    actual_run_id = await runtime.submit(agent.id, msg)  # type: ignore[union-attr]

    async for entry in runtime.tail(actual_run_id):  # type: ignore[union-attr]
        kind = entry.kind
        p = entry.payload or {}

        if kind == RunLogKind.TEXT_DELTA:
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

        elif kind == RunLogKind.REASONING_DELTA:
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

        elif kind == RunLogKind.TOOL_CALL:
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

        elif kind == RunLogKind.TOOL_RESULT:
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

        elif kind == RunLogKind.RUN_COMPLETED:
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

        elif kind == RunLogKind.RUN_FAILED:
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

        elif kind == RunLogKind.RUN_CANCELLED:
            return
