"""HITL response endpoint.

POST /chat/respond/{request_id} – resolve a pending tool-approval
or human-input request from the frontend.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from agent_framework.server.schemas import HITLResponse
from agent_framework.runtime.hitl import BridgeRegistry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["hitl"])


@router.post("/chat/respond/{request_id}")
async def respond_to_hitl(request_id: str, resp: HITLResponse, request: Request):
    """Resolve a pending HITL request (tool approval or human input)."""
    bridge_registry: BridgeRegistry = request.app.state.bridge_registry

    data = resp.model_dump(exclude_none=True)
    resolved = bridge_registry.resolve(request_id, data)

    if not resolved:
        raise HTTPException(
            status_code=404,
            detail=f"No pending HITL request with id={request_id!r}",
        )

    return {"status": "ok", "request_id": request_id}
