"""HITL response endpoint.

POST /chat/respond/{request_id} – resolve a pending tool-approval
or human-input request from the frontend.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from agent_framework.server.schemas import HITLResponse
from agent_framework.web_hitl import WebHITLBridge

logger = logging.getLogger(__name__)

router = APIRouter(tags=["hitl"])


@router.post("/chat/respond/{request_id}")
async def respond_to_hitl(request_id: str, resp: HITLResponse, request: Request):
    """Resolve a pending HITL request (tool approval or human input)."""
    bridge: WebHITLBridge = request.app.state.bridge

    data = resp.model_dump(exclude_none=True)
    resolved = bridge.resolve(request_id, data)

    if not resolved:
        return {
            "status": "error",
            "message": f"No pending request with id={request_id}",
        }

    return {"status": "ok", "request_id": request_id}
