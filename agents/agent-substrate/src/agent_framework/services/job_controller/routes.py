"""Job Controller — HTTP routes.

Routes:
  POST /jobs/runs          – start a new job run
  GET  /jobs/runs/{run_id} – get run status
  POST /jobs/runs/{id}/cancel – cancel a running job
  POST /jobs/runs/{id}/retry  – retry a failed job
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agent_framework.shared.database.dependency import get_db_session

from agent_framework.services.job_controller.service import (
    cancel_run,
    cleanup_cancel_signal,
    create_run,
    dispatch_run,
    get_active_run_for_thread,
    get_cancel_signal,
    get_run,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])


# ── Request/Response schemas ─────────────────────────────────────────────────


class RunCreateBody(BaseModel):
    thread_id: str
    user_content: str = ""
    system_instructions: str | None = None
    file_ids: list[str] | None = None
    client_request_id: str | None = None


class RunOut(BaseModel):
    run_id: str
    thread_id: str
    status: str
    user_content: str | None
    error_message: str | None
    steps_count: int
    created_at: str
    started_at: str | None
    completed_at: str | None


class CancelOut(BaseModel):
    status: str
    run_id: str
    thread_id: str


def _run_to_out(run) -> RunOut:
    return RunOut(
        run_id=str(run.id),
        thread_id=str(run.thread_id),
        status=run.status,
        user_content=run.user_content,
        error_message=run.error_message,
        steps_count=run.steps_count,
        created_at=run.created_at.isoformat(),
        started_at=run.started_at.isoformat() if run.started_at else None,
        completed_at=run.completed_at.isoformat() if run.completed_at else None,
    )


# ── Routes ───────────────────────────────────────────────────────────────────


@router.post("/runs", status_code=201)
async def create_run_endpoint(
    body: RunCreateBody,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Create and dispatch a workflow run.

    Single-flight: returns 409 if a run is already active for this thread.
    """
    thread_id = uuid.UUID(body.thread_id)

    # Single-flight check
    active = await get_active_run_for_thread(db, thread_id)
    if active:
        raise HTTPException(
            status_code=409,
            detail=f"A workflow run is already active for thread {body.thread_id}. "
            f"Cancel it first via POST /workflows/runs/{active.id}/cancel.",
        )

    run = await create_run(
        db,
        thread_id=thread_id,
        user_content=body.user_content,
        system_instructions=body.system_instructions,
        file_ids=body.file_ids,
        client_request_id=body.client_request_id,
    )
    await db.commit()

    # Dispatch run asynchronously
    asyncio.create_task(
        dispatch_run(
            run=run,
            event_bus=request.app.state.event_bus,
            session_factory=request.app.state.session_factory,
            redis_client=request.app.state.redis,
        )
    )

    return _run_to_out(run)


@router.get("/runs/{run_id}")
async def get_run_endpoint(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
):
    """Get the current status of a workflow run."""
    run = await get_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return _run_to_out(run)


@router.post("/runs/{run_id}/cancel")
async def cancel_run_endpoint(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
):
    """Cancel an active workflow run."""
    run = await get_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status not in ("pending", "running"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot cancel run in '{run.status}' state",
        )

    # Signal the agent to stop
    signal = get_cancel_signal(str(run_id))
    signal.set()
    cleanup_cancel_signal(str(run_id))

    # Update DB
    await cancel_run(db, run_id)
    return CancelOut(
        status="cancelled",
        run_id=str(run_id),
        thread_id=str(run.thread_id),
    )


@router.post("/threads/{thread_id}/cancel")
async def cancel_by_thread_endpoint(
    thread_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
):
    """Cancel the active workflow run for a thread (convenience for Gateway).

    Uses a distinct path (/threads/{thread_id}/cancel) to avoid ambiguity
    with /runs/{run_id}/cancel which has the same URL template pattern.
    """
    active = await get_active_run_for_thread(db, thread_id)
    if not active:
        return CancelOut(
            status="not_found",
            run_id="",
            thread_id=str(thread_id),
        )

    signal = get_cancel_signal(str(active.id))
    signal.set()
    cleanup_cancel_signal(str(active.id))

    await cancel_run(db, active.id)
    return CancelOut(
        status="cancelled",
        run_id=str(active.id),
        thread_id=str(thread_id),
    )
