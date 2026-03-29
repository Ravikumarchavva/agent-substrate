"""Live Stream Service — HTTP routes.

Routes:
  GET /stream/{thread_id}  — SSE stream for a thread/run
  GET /stream/health       — projector health
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stream", tags=["stream"])


@router.get("/{thread_id}")
async def stream_thread(
    thread_id: str,
    request: Request,
    run_id: Optional[str] = None,
):
    """SSE stream for a specific thread.

    Clients connect here to receive real-time agent events
    (text deltas, completions, tool results, HITL requests, etc.).
    """
    projector = request.app.state.projector

    return StreamingResponse(
        content=projector.stream_events(thread_id, run_id=run_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/health")
async def stream_health(request: Request):
    projector = request.app.state.projector
    subscriber_count = sum(len(s) for s in projector._subscribers.values())
    return {
        "status": "ok",
        "active_threads": len(projector._subscribers),
        "total_subscribers": subscriber_count,
    }
