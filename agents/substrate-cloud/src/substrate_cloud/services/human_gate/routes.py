"""Human Gate: HTTP routes (internal; the gateway serves them as the Agent API's HITL endpoints).

  POST /api/v1/hitl/respond/{request_id}   answer a pending approval or question
  GET  /api/v1/hitl/status/{thread_id}     what the thread's run is waiting on

A run that needs a person is suspended in the runtime's durable store, waiting on the signal ``hitl:<request_id>``. Answering is
sending that signal, so the service holds no state of its own and works from any replica.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request

from substrate_cloud.shared.auth.claims import AuthClaims
from substrate_cloud.shared.auth.middleware import get_current_user
from substrate_cloud.shared.contracts.human_gate import HITLResponse
from substrate_cloud.shared.problem import Problem
from substrate_cloud.stream.pending import pending_cards

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/hitl", tags=["hitl"])


def _token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    return header[7:] if header.lower().startswith("bearer ") else ""


async def _require_thread(request: Request, thread_id: str) -> None:
    """The conversation service answers 404 for a thread that is not the caller's."""
    await request.app.state.conversation_client.request(
        "GET", f"/threads/{thread_id}", _token(request)
    )


@router.post("/respond/{request_id}")
async def respond(
    request_id: str,
    body: HITLResponse,
    request: Request,
    user: AuthClaims = Depends(get_current_user),
) -> dict:
    """Resolve a pending approval or question. Who answered, and when, is what the service saw, never a value the client sent."""
    store = request.app.state.runtime.store
    waiting = await store.find_runs(wake_signal=f"hitl:{request_id}")
    run = waiting[0] if waiting else None
    # 404 for a request that is not waiting, and the same for one in another tenant or on another user's thread.
    if run is None or run.tenant != user.tenant_id or not run.thread_id:
        raise Problem(404, "not-found", f"No pending request with id={request_id!r}")
    await _require_thread(request, run.thread_id)

    data = body.model_dump(exclude_none=True)
    data["decided_by"] = user.sub
    data["decided_at"] = datetime.now(timezone.utc).isoformat()
    await store.signal(run.run_id, f"hitl:{request_id}", data)
    logger.info("Resolved request %s (run %s)", request_id, run.run_id)
    return {"status": "ok", "request_id": request_id}


@router.get("/status/{thread_id}")
async def status(
    thread_id: uuid.UUID,
    request: Request,
    user: AuthClaims = Depends(get_current_user),
) -> dict:
    await _require_thread(request, str(thread_id))
    return {"pending": await pending_cards(request.app.state.runtime, str(thread_id))}
