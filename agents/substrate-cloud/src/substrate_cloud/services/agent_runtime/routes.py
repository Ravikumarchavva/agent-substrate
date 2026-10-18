"""Agent Runtime: HTTP routes (internal; the job controller calls them with the caller's own token).

POST /api/v1/agent/runs   accept a run: build the thread's agent, submit it, and announce its end when it ends
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from substrate_cloud.services.agent_runtime.service import (
    announce_outcome,
    submit_run,
)
from substrate_cloud.shared.auth.claims import AuthClaims
from substrate_cloud.shared.auth.middleware import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent-runtime"])


class RunRequest(BaseModel):
    run_id: uuid.UUID  # the job controller's id for the run
    thread_id: uuid.UUID
    user_content: str
    system_instructions: Optional[str] = None
    file_ids: list[uuid.UUID] | None = None


class RunAccepted(BaseModel):
    run_id: str
    runtime_run_id: str
    status: str = "accepted"


@router.post("/runs", status_code=202)
async def start_agent_run(
    body: RunRequest,
    request: Request,
    user: AuthClaims = Depends(get_current_user),
) -> RunAccepted:
    """Accept a run and execute it asynchronously. Answers once the runtime holds it, with the id to stream it by."""
    state = request.app.state
    runtime_run_id = await submit_run(
        runtime=state.runtime,
        model_client=state.model_client,
        tools=state.tools,
        history=state.history,
        system_instructions=body.system_instructions or state.system_instructions,
        user=user,
        thread_id=str(body.thread_id),
        user_content=body.user_content,
    )

    task = asyncio.create_task(
        announce_outcome(
            runtime=state.runtime,
            event_bus=state.event_bus,
            job_run_id=str(body.run_id),
            runtime_run_id=runtime_run_id,
            thread_id=str(body.thread_id),
            tenant_id=user.tenant_id,
            user_id=user.sub,
        ),
        name=f"outcome-{body.run_id}",
    )
    state.outcome_tasks.add(task)

    def _done(t: asyncio.Task) -> None:
        state.outcome_tasks.discard(t)
        if not t.cancelled() and t.exception():
            logger.error("Watching run %s failed: %s", body.run_id, t.exception())

    task.add_done_callback(_done)
    return RunAccepted(run_id=str(body.run_id), runtime_run_id=runtime_run_id)
