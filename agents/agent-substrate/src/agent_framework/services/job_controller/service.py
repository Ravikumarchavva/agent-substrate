"""Job Controller — business logic.

Manages job run lifecycle: creation, start, completion, cancellation.
The Job Controller coordinates between Conversation Service, Agent Runtime,
and Stream services via events and direct calls.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agent_framework.services.job_controller.models import JobRun
from agent_framework.shared.events.bus import EventBus
from agent_framework.shared.events.types import workflow_failed, workflow_started

logger = logging.getLogger(__name__)


# ── Run CRUD ─────────────────────────────────────────────────────────────────


async def create_run(
    db: AsyncSession,
    *,
    thread_id: uuid.UUID,
    user_id: Optional[uuid.UUID] = None,
    user_content: str = "",
    system_instructions: Optional[str] = None,
    file_ids: Optional[list] = None,
    client_request_id: Optional[str] = None,
) -> JobRun:
    """Create a new job run in pending state."""
    # Idempotency: if a client_request_id is provided and already exists, return the existing run
    if client_request_id:
        result = await db.execute(
            select(JobRun).where(JobRun.client_request_id == client_request_id)
        )
        existing = result.scalar_one_or_none()
        if existing:
            return existing

    run = JobRun(
        thread_id=thread_id,
        user_id=user_id,
        user_content=user_content,
        system_instructions=system_instructions,
        file_ids=file_ids or [],
        client_request_id=client_request_id,
        status="pending",
    )
    db.add(run)
    await db.flush()
    return run


async def get_run(db: AsyncSession, run_id: uuid.UUID) -> Optional[JobRun]:
    result = await db.execute(select(JobRun).where(JobRun.id == run_id))
    return result.scalar_one_or_none()


async def get_active_run_for_thread(
    db: AsyncSession,
    thread_id: uuid.UUID,
) -> Optional[JobRun]:
    """Get any currently running job for a thread (single-flight check)."""
    result = await db.execute(
        select(JobRun)
        .where(
            JobRun.thread_id == thread_id,
            JobRun.status.in_(["pending", "running"]),
        )
        .order_by(JobRun.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def start_run(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> Optional[JobRun]:
    """Transition run from pending to running."""
    now = datetime.now(timezone.utc)
    await db.execute(
        update(JobRun)
        .where(JobRun.id == run_id, JobRun.status == "pending")
        .values(status="running", started_at=now)
    )
    await db.flush()
    return await get_run(db, run_id)


async def complete_run(
    db: AsyncSession,
    run_id: uuid.UUID,
    *,
    steps_count: int = 0,
) -> Optional[JobRun]:
    """Mark a run as completed."""
    now = datetime.now(timezone.utc)
    await db.execute(
        update(JobRun)
        .where(JobRun.id == run_id)
        .values(
            status="completed",
            completed_at=now,
            steps_count=steps_count,
        )
    )
    await db.flush()
    return await get_run(db, run_id)


async def fail_run(
    db: AsyncSession,
    run_id: uuid.UUID,
    *,
    error_message: str = "",
) -> Optional[JobRun]:
    """Mark a run as failed."""
    now = datetime.now(timezone.utc)
    await db.execute(
        update(JobRun)
        .where(JobRun.id == run_id)
        .values(
            status="failed",
            completed_at=now,
            error_message=error_message,
        )
    )
    await db.flush()
    return await get_run(db, run_id)


async def cancel_run(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> Optional[JobRun]:
    """Mark a run as cancelled."""
    now = datetime.now(timezone.utc)
    await db.execute(
        update(JobRun)
        .where(
            JobRun.id == run_id,
            JobRun.status.in_(["pending", "running"]),
        )
        .values(status="cancelled", completed_at=now)
    )
    await db.flush()
    return await get_run(db, run_id)


# ── Orchestration ────────────────────────────────────────────────────────────

# In-process cancel signals keyed by run_id. In a multi-replica setup these
# would be replaced by Redis pub/sub or shared cancel flags.
_cancel_signals: Dict[str, asyncio.Event] = {}


def get_cancel_signal(run_id: str) -> asyncio.Event:
    """Get or create a cancel signal for a run."""
    if run_id not in _cancel_signals:
        _cancel_signals[run_id] = asyncio.Event()
    return _cancel_signals[run_id]


def cleanup_cancel_signal(run_id: str) -> None:
    _cancel_signals.pop(run_id, None)


async def dispatch_run(
    *,
    run: JobRun,
    event_bus: EventBus,
    session_factory,
    redis_client,
) -> None:
    """Dispatch a job run to the Agent Runtime.

    This is the core orchestration logic. It:
    1. Transitions the run to 'running'
    2. Publishes a workflow_started event
    3. Sends the run command to the Agent Runtime via event bus
    4. Monitors for completion or cancellation

    The Agent Runtime subscribes to the run command event and
    executes the agent, publishing progress events back.
    """
    run_id = str(run.id)
    thread_id = str(run.thread_id)

    try:
        # Transition to running
        async with session_factory() as db:
            run = await start_run(db, run.id)
            await db.commit()

        # Publish start event
        event = workflow_started(
            run_id=run_id,
            thread_id=thread_id,
            user_content=run.user_content or "",
            correlation_id=run_id,
        )
        await event_bus.publish(event)

        # Publish run command for Agent Runtime to pick up
        run_command = {
            "type": "agent.run_command",
            "run_id": run_id,
            "thread_id": thread_id,
            "user_content": run.user_content or "",
            "system_instructions": run.system_instructions or "",
            "file_ids": run.file_ids or [],
        }
        from agent_framework.shared.events.envelope import EventEnvelope

        cmd_envelope = EventEnvelope(
            event_type="agent.run_command",
            payload=run_command,
            correlation_id=run_id,
        )
        await event_bus.publish(cmd_envelope)

        logger.info("Dispatched job run %s for thread %s", run_id, thread_id)

    except Exception as exc:
        logger.exception("Failed to dispatch job run %s", run_id)
        async with session_factory() as db:
            await fail_run(db, run.id, error_message=str(exc))
            await db.commit()

        error_event = workflow_failed(
            run_id=run_id,
            thread_id=thread_id,
            error=str(exc),
            correlation_id=run_id,
        )
        await event_bus.publish(error_event)
