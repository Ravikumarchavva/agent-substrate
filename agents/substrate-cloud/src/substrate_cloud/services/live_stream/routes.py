"""Live Stream: HTTP routes (internal; the gateway serves them as the Agent API's stream endpoints).

  GET /api/v1/stream/{thread_id}        reconnect to the thread's active run (the rest of it)
  GET /api/v1/stream/runs/{run_id}      a run from its start, by the runtime's id for it (what ``POST /chat`` streams)

Both answer 404 for a thread or run that is not the caller's.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from substrate_cloud.services.live_stream.service import (
    PROTOCOL_HEADERS,
    done_only,
    run_stream,
)
from substrate_cloud.shared.auth.claims import AuthClaims
from substrate_cloud.shared.auth.middleware import get_current_user
from substrate_cloud.shared.problem import Problem

router = APIRouter(prefix="/stream", tags=["stream"])


def _token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    return header[7:] if header.lower().startswith("bearer ") else ""


async def _require_thread(request: Request, thread_id: str) -> None:
    """The conversation service refuses (404) a thread that is not the caller's."""
    await request.app.state.conversation_client.request(
        "GET", f"/threads/{thread_id}", _token(request)
    )


def _sse(lines) -> StreamingResponse:
    return StreamingResponse(
        lines, media_type="text/event-stream", headers=PROTOCOL_HEADERS
    )


@router.get("/runs/{run_id}")
async def stream_run(
    run_id: str,
    request: Request,
    user: AuthClaims = Depends(get_current_user),
) -> StreamingResponse:
    runtime = request.app.state.runtime
    run = await runtime.get_run(run_id)
    if run is None or run.tenant != user.tenant_id or not run.thread_id:
        raise Problem(404, "not-found", "Run not found")
    await _require_thread(request, run.thread_id)
    return _sse(run_stream(runtime, run_id))


@router.get("/{thread_id}")
async def stream_thread(
    thread_id: uuid.UUID,
    request: Request,
    user: AuthClaims = Depends(get_current_user),
) -> StreamingResponse:
    """For a browser that lost its connection while the run went on: the rest of the active run, or a clean ``[DONE]`` when none is."""
    await _require_thread(request, str(thread_id))
    runtime = request.app.state.runtime
    found = await runtime.active_run_for_thread(str(thread_id))
    if found is None or found.tenant != user.tenant_id:
        return _sse(done_only())
    from_seq = await runtime.store.last_seq(found.run_id) + 1
    return _sse(run_stream(runtime, found.run_id, from_seq=from_seq))
