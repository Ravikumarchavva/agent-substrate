"""Job Controller: business logic.

A run's lifecycle is pending -> running -> completed | failed | cancelled. The controller creates and starts it; the agent runtime
announces how it ended (``workflow.run_*`` events) and ``consume_outcomes`` records that. Every lookup is under the caller's
tenant and, unless the caller administers the tenant, under the caller's user: someone else's run is simply not found.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from substrate_cloud.services.job_controller.models import JobRun
from substrate_cloud.shared.auth.claims import AuthClaims
from substrate_cloud.shared.events import types as events

logger = logging.getLogger(__name__)

ACTIVE = ("pending", "running")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Run CRUD ─────────────────────────────────────────────────────────────────


async def create_run(
    db: AsyncSession,
    claims: AuthClaims,
    *,
    thread_id: uuid.UUID,
    user_content: str = "",
    system_instructions: Optional[str] = None,
    file_ids: Optional[list] = None,
    client_request_id: Optional[str] = None,
) -> JobRun:
    """A new run in pending state for the caller."""
    run = JobRun(
        thread_id=thread_id,
        tenant_id=claims.tenant_id,
        user_id=claims.sub,
        user_content=user_content,
        system_instructions=system_instructions,
        file_ids=file_ids or [],
        client_request_id=client_request_id,
        status="pending",
    )
    db.add(run)
    await db.flush()
    return run


def _visible(claims: AuthClaims):
    """The WHERE clauses of what the caller may see: their tenant, and their own runs unless they administer it."""
    clauses = [JobRun.tenant_id == claims.tenant_id]
    if not claims.is_admin:
        clauses.append(JobRun.user_id == claims.sub)
    return clauses


async def find_by_request_id(
    db: AsyncSession, claims: AuthClaims, client_request_id: str
) -> Optional[JobRun]:
    """The run this caller already started under an idempotency key, if any."""
    return (
        await db.execute(
            select(JobRun).where(
                JobRun.tenant_id == claims.tenant_id,
                JobRun.user_id == claims.sub,
                JobRun.client_request_id == client_request_id,
            )
        )
    ).scalar_one_or_none()


async def get_run(
    db: AsyncSession, claims: AuthClaims, run_id: uuid.UUID
) -> Optional[JobRun]:
    return (
        await db.execute(select(JobRun).where(JobRun.id == run_id, *_visible(claims)))
    ).scalar_one_or_none()


async def get_active_run_for_thread(
    db: AsyncSession, claims: AuthClaims, thread_id: uuid.UUID
) -> Optional[JobRun]:
    """The run still going on a thread (the single-flight check)."""
    return (
        await db.execute(
            select(JobRun)
            .where(
                JobRun.thread_id == thread_id,
                JobRun.status.in_(ACTIVE),
                *_visible(claims),
            )
            .order_by(JobRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _settle(
    db: AsyncSession, run_id: uuid.UUID, **values: Any
) -> Optional[JobRun]:
    """Move a run that is still going to its end state. A run that already ended stays as it ended."""
    await db.execute(
        update(JobRun)
        .where(JobRun.id == run_id, JobRun.status.in_(ACTIVE))
        .values(completed_at=_now(), **values)
    )
    await db.flush()
    return (
        await db.execute(select(JobRun).where(JobRun.id == run_id))
    ).scalar_one_or_none()


async def start_run(
    db: AsyncSession, run_id: uuid.UUID, *, runtime_run_id: str
) -> Optional[JobRun]:
    """pending -> running, once the agent runtime has accepted the run."""
    await db.execute(
        update(JobRun)
        .where(JobRun.id == run_id, JobRun.status == "pending")
        .values(status="running", started_at=_now(), runtime_run_id=runtime_run_id)
    )
    await db.flush()
    return (
        await db.execute(select(JobRun).where(JobRun.id == run_id))
    ).scalar_one_or_none()


async def complete_run(
    db: AsyncSession, run_id: uuid.UUID, *, steps_count: int = 0
) -> Optional[JobRun]:
    return await _settle(db, run_id, status="completed", steps_count=steps_count)


async def fail_run(
    db: AsyncSession, run_id: uuid.UUID, *, error_message: str = ""
) -> Optional[JobRun]:
    return await _settle(db, run_id, status="failed", error_message=error_message)


async def cancel_run(db: AsyncSession, run_id: uuid.UUID) -> Optional[JobRun]:
    return await _settle(db, run_id, status="cancelled")


# ── Consuming how runs ended ─────────────────────────────────────────────────


async def apply_outcome(session_factory: Any, envelope: Any) -> None:
    """Record a ``workflow.run_*`` event on its run."""
    payload = envelope.payload
    try:
        run_id = uuid.UUID(payload["run_id"])
    except (KeyError, ValueError):
        logger.warning("Outcome event without a usable run id: %s", envelope.event_id)
        return
    async with session_factory() as db:
        if envelope.event_type == events.RUN_COMPLETED:
            await complete_run(db, run_id, steps_count=int(payload.get("steps", 0)))
        elif envelope.event_type == events.RUN_FAILED:
            await fail_run(db, run_id, error_message=str(payload.get("error", "")))
        elif envelope.event_type == events.RUN_CANCELLED:
            await cancel_run(db, run_id)
        await db.commit()


async def consume_outcomes(app: Any) -> None:
    """Background task: finish the runs the agent runtime reports on (completed, failed or cancelled)."""

    async def follow(event_type: str) -> None:
        async for envelope in app.state.event_bus.subscribe(
            event_type, group="job-controller"
        ):
            try:
                await apply_outcome(app.state.session_factory, envelope)
            except Exception:
                logger.exception("Could not apply %s", envelope.event_type)

    await asyncio.gather(
        follow(events.RUN_COMPLETED),
        follow(events.RUN_FAILED),
        follow(events.RUN_CANCELLED),
    )
