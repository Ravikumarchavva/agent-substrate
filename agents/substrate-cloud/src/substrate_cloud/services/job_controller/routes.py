"""Job Controller: HTTP routes (internal; the gateway calls them with the caller's own token).

POST /api/v1/jobs/runs                       start a run on a thread the caller owns
GET  /api/v1/jobs/runs/{run_id}              a run's status
POST /api/v1/jobs/runs/{run_id}/cancel       cancel a running job
POST /api/v1/jobs/threads/{thread_id}/cancel cancel whatever is running on a thread
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from substrate_cloud.services.job_controller import service
from substrate_cloud.services.job_controller.models import JobRun
from substrate_cloud.shared.auth.claims import AuthClaims
from substrate_cloud.shared.auth.middleware import get_current_user
from substrate_cloud.shared.database.dependency import get_db_session
from substrate_cloud.shared.events import types as events
from substrate_cloud.shared.problem import Problem

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])


class RunCreateBody(BaseModel):
    thread_id: uuid.UUID
    user_content: str = ""
    system_instructions: str | None = None
    file_ids: list[uuid.UUID] | None = None
    client_request_id: str | None = None


class RunOut(BaseModel):
    run_id: str
    thread_id: str
    status: str
    runtime_run_id: str | None
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


def _run_out(run: JobRun) -> RunOut:
    return RunOut(
        run_id=str(run.id),
        thread_id=str(run.thread_id),
        status=run.status,
        runtime_run_id=run.runtime_run_id,
        user_content=run.user_content,
        error_message=run.error_message,
        steps_count=run.steps_count,
        created_at=run.created_at.isoformat(),
        started_at=run.started_at.isoformat() if run.started_at else None,
        completed_at=run.completed_at.isoformat() if run.completed_at else None,
    )


def _token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    return header[7:] if header.lower().startswith("bearer ") else ""


@router.post("/runs", status_code=201)
async def create_run_endpoint(
    body: RunCreateBody,
    request: Request,
    user: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> RunOut:
    """Start a run on a thread the caller owns and hand it to the agent runtime.

    Single-flight: 409 while a run is still going on the thread. The runtime has accepted the run (and has its own id for it) when
    this answers, so the caller can stream it straight away.
    """
    token = _token(request)
    # The conversation service answers 404 for a thread that is not the caller's, whoever else's it is.
    await request.app.state.conversation_client.request(
        "GET", f"/threads/{body.thread_id}", token
    )

    if body.client_request_id:  # the same key again is the same run, not a second one
        again = await service.find_by_request_id(db, user, body.client_request_id)
        if again is not None:
            return _run_out(again)

    if await service.get_active_run_for_thread(db, user, body.thread_id):
        raise Problem(
            409,
            "conflict",
            f"A run is already active on thread {body.thread_id}; cancel it first.",
        )
    run = await service.create_run(
        db,
        user,
        thread_id=body.thread_id,
        user_content=body.user_content,
        system_instructions=body.system_instructions,
        file_ids=[str(f) for f in body.file_ids or []],
        client_request_id=body.client_request_id,
    )
    await db.commit()
    bus = request.app.state.event_bus
    try:
        accepted = await request.app.state.runtime_client.json(
            "POST",
            "/agent/runs",
            token,
            json={
                "run_id": str(run.id),
                "thread_id": str(run.thread_id),
                "user_content": run.user_content or "",
                "system_instructions": run.system_instructions,
                "file_ids": run.file_ids or [],
            },
            timeout=30.0,
        )
    except Problem as problem:
        logger.warning("Agent runtime refused run %s: %s", run.id, problem.detail)
        await service.fail_run(db, run.id, error_message=problem.detail)
        await db.commit()
        await bus.publish(
            events.workflow_failed(
                str(run.id),
                str(run.thread_id),
                tenant_id=user.tenant_id,
                user_id=user.sub,
                error=problem.detail,
            )
        )
        raise

    started = await service.start_run(
        db, run.id, runtime_run_id=accepted["runtime_run_id"]
    )
    await db.commit()
    await bus.publish(
        events.workflow_started(
            str(run.id), str(run.thread_id), tenant_id=user.tenant_id, user_id=user.sub
        )
    )
    return _run_out(started or run)


@router.get("/runs/{run_id}")
async def get_run_endpoint(
    run_id: uuid.UUID,
    user: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> RunOut:
    run = await service.get_run(db, user, run_id)
    if run is None:
        raise Problem(404, "not-found", "Run not found")
    return _run_out(run)


async def _cancel(
    request: Request, db: AsyncSession, user: AuthClaims, run: JobRun
) -> CancelOut:
    await service.cancel_run(db, run.id)
    await db.commit()
    # The agent runtime stops the run itself; it is the one holding it.
    await request.app.state.event_bus.publish(
        events.job_cancel_requested(
            str(run.id),
            str(run.thread_id),
            tenant_id=run.tenant_id,
            user_id=run.user_id,
            runtime_run_id=run.runtime_run_id or "",
        )
    )
    return CancelOut(
        status="cancelled", run_id=str(run.id), thread_id=str(run.thread_id)
    )


@router.post("/runs/{run_id}/cancel")
async def cancel_run_endpoint(
    run_id: uuid.UUID,
    request: Request,
    user: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> CancelOut:
    run = await service.get_run(db, user, run_id)
    if run is None:
        raise Problem(404, "not-found", "Run not found")
    if run.status not in service.ACTIVE:
        raise Problem(409, "conflict", f"Cannot cancel a run that is {run.status}")
    return await _cancel(request, db, user, run)


@router.post("/threads/{thread_id}/cancel")
async def cancel_by_thread_endpoint(
    thread_id: uuid.UUID,
    request: Request,
    user: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> CancelOut:
    """Cancel the run going on a thread (the public ``POST /chat/{thread_id}/cancel``)."""
    # 404 for a thread that is not the caller's, as everywhere else.
    await request.app.state.conversation_client.request(
        "GET", f"/threads/{thread_id}", _token(request)
    )
    run = await service.get_active_run_for_thread(db, user, thread_id)
    if run is None:
        return CancelOut(status="not_found", run_id="", thread_id=str(thread_id))
    return await _cancel(request, db, user, run)
