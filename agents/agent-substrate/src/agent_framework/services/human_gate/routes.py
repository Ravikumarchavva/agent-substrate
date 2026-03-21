"""Human Gate Service — HTTP routes.

Routes:
  POST /hitl/respond/{request_id}  — respond to a HITL request
  GET  /hitl/status/{thread_id}    — get pending HITL requests for a thread
  GET  /hitl/request/{request_id}  — get a specific HITL request
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agent_framework.services.human_gate.service import (
    cancel_pending_for_thread,
    get_pending_for_thread,
    get_request,
    resolve_request,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/hitl", tags=["hitl"])


class HITLResponseBody(BaseModel):
    approved: Optional[bool] = None
    value: Optional[str] = None
    responded_by: Optional[str] = None


class HITLRequestOut(BaseModel):
    request_id: str
    thread_id: str
    type: str
    tool_name: Optional[str]
    prompt: Optional[str]
    options: Optional[list]
    status: str
    response_value: Optional[str]
    created_at: str
    resolved_at: Optional[str]


async def _get_db(request: Request):
    async with request.app.state.session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _to_out(req) -> HITLRequestOut:
    return HITLRequestOut(
        request_id=req.request_id,
        thread_id=str(req.thread_id),
        type=req.type,
        tool_name=req.tool_name,
        prompt=req.prompt,
        options=req.options,
        status=req.status,
        response_value=req.response_value,
        created_at=req.created_at.isoformat(),
        resolved_at=req.resolved_at.isoformat() if req.resolved_at else None,
    )


@router.post("/respond/{request_id}")
async def respond_to_request(
    request_id: str,
    body: HITLResponseBody,
    request: Request,
    db: AsyncSession = Depends(_get_db),
):
    """Respond to a pending HITL request."""
    req = await get_request(db, request_id)
    if not req:
        raise HTTPException(status_code=404, detail="HITL request not found")
    if req.status != "pending":
        raise HTTPException(status_code=409, detail=f"Request already resolved: {req.status}")

    # Determine status from response
    if req.type == "tool_approval":
        status = "approved" if body.approved else "rejected"
        response_value = body.value or status
    else:
        status = "answered"
        response_value = body.value or ""

    resolved = await resolve_request(
        db,
        request_id,
        status=status,
        response_value=response_value,
        responded_by=body.responded_by,
        redis_client=request.app.state.redis,
        event_bus=request.app.state.event_bus,
    )

    return _to_out(resolved)


@router.get("/status/{thread_id}")
async def get_thread_status(
    thread_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(_get_db),
):
    """Get all pending HITL requests for a thread."""
    pending = await get_pending_for_thread(db, thread_id)
    return {
        "thread_id": str(thread_id),
        "pending_count": len(pending),
        "requests": [_to_out(r) for r in pending],
    }


@router.get("/request/{request_id}")
async def get_request_endpoint(
    request_id: str,
    db: AsyncSession = Depends(_get_db),
):
    req = await get_request(db, request_id)
    if not req:
        raise HTTPException(status_code=404, detail="HITL request not found")
    return _to_out(req)


@router.post("/cancel/{thread_id}")
async def cancel_thread_requests(
    thread_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(_get_db),
):
    """Cancel all pending HITL requests for a thread."""
    count = await cancel_pending_for_thread(
        db,
        thread_id,
        redis_client=request.app.state.redis,
    )
    return {"thread_id": str(thread_id), "cancelled_count": count}
