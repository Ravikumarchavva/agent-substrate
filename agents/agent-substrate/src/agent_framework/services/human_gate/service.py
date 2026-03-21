"""Human Gate Service — business logic.

Manages the lifecycle of human-in-the-loop approval and input requests.
Uses Redis pub/sub to deliver responses back to the waiting agent.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import redis.asyncio as aioredis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agent_framework.services.human_gate.models import HITLRequest
from agent_framework.shared.events.bus import EventBus
from agent_framework.shared.events.envelope import EventEnvelope

logger = logging.getLogger(__name__)


# Redis channel pattern for HITL responses
HITL_RESPONSE_CHANNEL = "hitl:response:{request_id}"


async def create_request(
    db: AsyncSession,
    *,
    request_id: str,
    thread_id: uuid.UUID,
    run_id: Optional[str] = None,
    type: str,
    tool_name: Optional[str] = None,
    tool_input: Optional[Dict[str, Any]] = None,
    prompt: Optional[str] = None,
    options: Optional[list] = None,
) -> HITLRequest:
    """Create a new pending HITL request."""
    req = HITLRequest(
        request_id=request_id,
        thread_id=thread_id,
        run_id=run_id,
        type=type,
        tool_name=tool_name,
        tool_input=tool_input,
        prompt=prompt,
        options=options,
        status="pending",
    )
    db.add(req)
    await db.flush()
    return req


async def get_request(db: AsyncSession, request_id: str) -> Optional[HITLRequest]:
    result = await db.execute(
        select(HITLRequest).where(HITLRequest.request_id == request_id)
    )
    return result.scalar_one_or_none()


async def get_pending_for_thread(
    db: AsyncSession,
    thread_id: uuid.UUID,
) -> List[HITLRequest]:
    result = await db.execute(
        select(HITLRequest)
        .where(
            HITLRequest.thread_id == thread_id,
            HITLRequest.status == "pending",
        )
        .order_by(HITLRequest.created_at)
    )
    return list(result.scalars().all())


async def resolve_request(
    db: AsyncSession,
    request_id: str,
    *,
    status: str,
    response_value: Optional[str] = None,
    responded_by: Optional[str] = None,
    redis_client: Optional[aioredis.Redis] = None,
    event_bus: Optional[EventBus] = None,
) -> Optional[HITLRequest]:
    """Resolve a HITL request and notify the waiting agent via Redis pub/sub."""
    now = datetime.now(timezone.utc)
    await db.execute(
        update(HITLRequest)
        .where(
            HITLRequest.request_id == request_id,
            HITLRequest.status == "pending",
        )
        .values(
            status=status,
            response_value=response_value,
            responded_by=responded_by,
            resolved_at=now,
        )
    )
    await db.flush()

    req = await get_request(db, request_id)
    if not req:
        return None

    # Publish response via Redis pub/sub for the waiting agent
    if redis_client:
        channel = HITL_RESPONSE_CHANNEL.format(request_id=request_id)
        response_data = json.dumps({
            "request_id": request_id,
            "status": status,
            "value": response_value,
            "responded_by": responded_by,
        })
        await redis_client.publish(channel, response_data)

    # Publish event for observability
    if event_bus:
        await event_bus.publish(EventEnvelope(
            event_type="hitl.request_resolved",
            correlation_id=req.run_id or request_id,
            payload={
                "type": "hitl.request_resolved",
                "request_id": request_id,
                "thread_id": str(req.thread_id),
                "hitl_type": req.type,
                "status": status,
                "response_value": response_value,
            },
        ))

    return req


async def cancel_pending_for_thread(
    db: AsyncSession,
    thread_id: uuid.UUID,
    *,
    reason: str = "cancelled",
    redis_client: Optional[aioredis.Redis] = None,
) -> int:
    """Cancel all pending HITL requests for a thread. Returns count cancelled."""
    pending = await get_pending_for_thread(db, thread_id)
    for req in pending:
        await resolve_request(
            db,
            req.request_id,
            status="cancelled",
            response_value=reason,
            redis_client=redis_client,
        )
    return len(pending)
