"""Cancel endpoint — aborts a running agent stream for a given thread.

POST /chat/{thread_id}/cancel
  Resolves the thread's active run_id durably (``Scheduler.find_run_for_thread``
  — a DB query, not an in-process registry) and cancels it via
  ``Supervisor.cancel()``. This works regardless of which replica is actually
  running the stream: the cancel is observed either almost-instantly (if this
  request happens to land on the same replica, via the best-effort
  ``Runtime.cancel()`` fast path) or within one heartbeat interval (via the
  durable ``cancel_requested`` column — see ``PostgresScheduler.heartbeat``),
  and the streaming replica notices via the EventLog's ``run.cancelled``
  entry either way (see ``AgentStreamSession._check_disconnect``'s docstring).
"""

from __future__ import annotations
from substrate.logger import setup_logging

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.kernel.core.identity import AgentId
from substrate.kernel.runtime.supervisor import RunHandle
from substrate.serving.monolith.database import get_db
from substrate.serving.monolith.dependencies import ServerDependencies, get_ctx
from substrate.serving.monolith.security.deps import AuthClaims, get_current_user
from substrate.serving.monolith.services import get_owned_thread

logger = setup_logging()

router = APIRouter(
    tags=["chat"],
)


@router.post("/chat/{thread_id}/cancel")
async def cancel_chat(
    thread_id: uuid.UUID,
    ctx: ServerDependencies = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
    user: AuthClaims = Depends(get_current_user),
):
    """Cancel the active run for *thread_id*, wherever it's actually running.

    Returns ``{"status": "cancelled"}`` if an active run was found,
    or ``{"status": "not_found"}`` if nothing was active for that thread.
    """
    if not await get_owned_thread(db, thread_id, user):
        raise HTTPException(status_code=404, detail="Thread not found")

    runtime = ctx.runtime
    if runtime is None:
        raise HTTPException(status_code=503, detail="Runtime not configured")

    found = await runtime.scheduler.find_run_for_thread(str(thread_id))
    if found is None:
        logger.debug(
            "Cancel requested for thread %s but no active run found", thread_id
        )
        return {"status": "not_found", "thread_id": str(thread_id)}

    run_id, _status = found
    # agent_id/parent_run are unused by Supervisor.cancel() (it only reads
    # handle.run_id — see PostgresSupervisor.cancel()); find_run_for_thread
    # doesn't resolve the agent, so these are placeholders, not real values.
    handle = RunHandle(run_id=run_id, agent_id=AgentId(type="", key=""), parent_run="")

    # Best-effort fast path: if this request happens to land on the replica
    # actually running the task, this cancels its local CancellationToken
    # immediately instead of waiting out a heartbeat interval.
    await runtime.cancel(run_id)
    # Durable, cross-replica cascade: always correct regardless of which
    # replica owns the run (see module docstring).
    await runtime.supervisor.cancel(handle, reason="user_requested")

    logger.info("Cancellation requested for thread %s (run %s)", thread_id, run_id)
    return {"status": "cancelled", "thread_id": str(thread_id)}
