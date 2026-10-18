"""Agent Runtime service logic.

The runtime runs the agent and journals everything it does in the shared store (the record ``live_stream`` serves and
``conversation`` projects). What this module adds is the platform's side: the run belongs to the token's tenant and user, and its end is
announced as a ``workflow.run_*`` event for the job controller and the audit trail.
"""

from __future__ import annotations

import logging
from typing import Any

from substrate.runtime import ChatPayload, Message
from substrate.types import (
    Actor,
    ChatMessage,
    Role,
    RunLogKind,
    TextBlock,
    ThreadBusyError,
)

from substrate_cloud.factory import build_agent_for_run
from substrate_cloud.shared.auth.claims import AuthClaims
from substrate_cloud.shared.events import types as events
from substrate_cloud.shared.problem import Problem

logger = logging.getLogger(__name__)


async def submit_run(
    *,
    runtime: Any,
    model_client: Any,
    tools: list,
    history: Any,
    system_instructions: str,
    user: AuthClaims,
    thread_id: str,
    user_content: str,
) -> str:
    """Build the thread's agent, register it and hand the run to the runtime. Returns the runtime's id for the run.

    Tenant and user come from the verified token and travel with the run (its scope), exactly as in the monolith's chat route. A
    thread has one active run at a time, enforced by the runtime across every replica: a second submit is a 409.
    """
    agent = build_agent_for_run(
        model_client=model_client,
        tools=tools,
        system_instructions=system_instructions,
        history=history,
        tenant_id=user.tenant_id,
        session_id=thread_id,
    )
    await runtime.register(agent, pinned=False)
    message = Message(
        target=agent.id,
        sender=Actor(type="http_proxy"),
        payload=ChatPayload(
            message=ChatMessage(role=Role.USER, content=[TextBlock(text=user_content)])
        ),
        correlation_id=thread_id,
        metadata={
            "display_text": user_content,
            "attachments": [],
            "user_id": user.sub,
            "tenant_id": user.tenant_id,
            "branch_id": "main",
        },
    )
    try:
        return str(
            await runtime.submit(
                agent.id,
                message,
                max_retries=0,
                thread_id=thread_id,
                tenant=user.tenant_id,
            )
        )
    except ThreadBusyError as exc:
        raise Problem(
            409, "conflict", f"A run is already active on thread {thread_id}"
        ) from exc


async def announce_outcome(
    *,
    runtime: Any,
    event_bus: Any,
    job_run_id: str,
    runtime_run_id: str,
    thread_id: str,
    tenant_id: str,
    user_id: str,
) -> None:
    """Follow the run's journal to its end and publish how it ended (``workflow.run_completed|failed|cancelled``)."""
    ids = {"tenant_id": tenant_id, "user_id": user_id}
    steps = 0
    async for entry in runtime.tail(runtime_run_id):
        kind = entry.kind
        if kind == RunLogKind.TOOL_RESULT:
            steps += 1
        elif kind == RunLogKind.RUN_COMPLETED:
            await event_bus.publish(
                events.workflow_completed(job_run_id, thread_id, steps=steps, **ids)
            )
            return
        elif kind == RunLogKind.RUN_FAILED:
            error = (entry.payload or {}).get("error", "agent run failed")
            logger.error("Run %s failed: %s", job_run_id, error)
            await event_bus.publish(
                events.workflow_failed(job_run_id, thread_id, error=str(error), **ids)
            )
            return
        elif kind == RunLogKind.RUN_CANCELLED:
            await event_bus.publish(
                events.workflow_cancelled(job_run_id, thread_id, **ids)
            )
            return


async def cancel_requested(runtime: Any, envelope: Any) -> None:
    """Stop the run a ``job.cancel_requested`` event names (this replica or, via the durable flag, whichever one holds it)."""
    payload = envelope.payload
    run_id = payload.get("runtime_run_id") or ""
    if not run_id:
        found = await runtime.active_run_for_thread(payload.get("thread_id", ""))
        run_id = found.run_id if found else ""
    if run_id:
        logger.info("Cancelling run %s", run_id)
        await runtime.cancel(run_id, reason="user_requested")
