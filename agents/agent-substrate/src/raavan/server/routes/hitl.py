"""HITL response endpoint.

POST /chat/respond/{request_id} – resolve a pending tool-approval
or human-input request from the frontend.

GET /hitl/status/{thread_id} – check for pending HITL requests
(used by the frontend on reconnect to restore approval/input cards).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from raavan.server.schemas import HITLResponse
from raavan.server.context import ServerContext, get_ctx

logger = logging.getLogger(__name__)

router = APIRouter(tags=["hitl"])


@router.post("/chat/respond/{request_id}")
async def respond_to_hitl(
    request_id: str,
    resp: HITLResponse,
    ctx: ServerContext = Depends(get_ctx),
):
    """Resolve a pending HITL request (tool approval or human input)."""
    data = resp.model_dump(exclude_none=True)
    resolved = ctx.bridge_registry.resolve(request_id, data)

    if not resolved:
        raise HTTPException(
            status_code=404,
            detail=f"No pending HITL request with id={request_id!r}",
        )

    return {"status": "ok", "request_id": request_id}


@router.get("/hitl/status/{thread_id}")
async def hitl_status(
    thread_id: str,
    ctx: ServerContext = Depends(get_ctx),
):
    """Return pending HITL requests for a thread.

    The frontend calls this on reconnect / page load to check if the agent
    is blocked waiting for user input so it can restore approval or
    human-input cards without re-sending the chat message.

    Returns:
        ``{"pending": [...]}`` — list of pending HITL event payloads.
        Empty list if no HITL is pending.
    """
    pending = ctx.bridge_registry.get_pending_hitl(thread_id)
    return {"pending": pending}
