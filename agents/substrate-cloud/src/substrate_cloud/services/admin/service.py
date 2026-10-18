"""Admin Control Plane: business logic.

The audit trail: written from the domain events other services publish, read by platform administrators.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from substrate_cloud.services.admin.models import AuditLog
from substrate_cloud.shared.events import types as events

logger = logging.getLogger(__name__)


async def write_audit_log(
    db: AsyncSession,
    *,
    action: str,
    tenant_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
) -> AuditLog:
    entry = AuditLog(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details or {},
        ip_address=ip_address,
    )
    db.add(entry)
    await db.flush()
    return entry


async def get_audit_logs(
    db: AsyncSession,
    *,
    tenant_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    action: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[AuditLog]:
    query = select(AuditLog).order_by(AuditLog.created_at.desc())
    if tenant_id:
        query = query.where(AuditLog.tenant_id == tenant_id)
    if actor_id:
        query = query.where(AuditLog.actor_id == actor_id)
    if action:
        query = query.where(AuditLog.action == action)
    return list((await db.execute(query.limit(limit).offset(offset))).scalars().all())


async def get_platform_stats(db: AsyncSession) -> Dict[str, Any]:
    entries = (await db.execute(select(func.count(AuditLog.id)))).scalar() or 0
    by_action = (
        await db.execute(
            select(AuditLog.action, func.count()).group_by(AuditLog.action)
        )
    ).all()
    return {"audit_entries": entries, "by_action": {a: n for a, n in by_action}}


# ── The audit trail from events ──────────────────────────────────────────────


def audit_entry(envelope: Any) -> Dict[str, Any]:
    """The audit-log fields of a domain event."""
    payload = dict(envelope.payload)
    tenant_id = payload.pop("tenant_id", None)
    actor_id = payload.pop("user_id", None)
    if envelope.event_type.startswith("conversation."):
        resource_type, resource_id = "thread", payload.pop("thread_id", None)
    else:
        resource_type, resource_id = "run", payload.pop("run_id", None)
    return {
        "action": envelope.event_type,
        "tenant_id": tenant_id,
        "actor_id": actor_id,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "details": payload,
    }


async def record_event(session_factory: Any, envelope: Any) -> None:
    async with session_factory() as db:
        await write_audit_log(db, **audit_entry(envelope))
        await db.commit()


async def consume_audit(app: Any) -> None:
    """Background task: every audited event becomes one audit-log entry."""

    async def follow(event_type: str) -> None:
        async for envelope in app.state.event_bus.subscribe(
            event_type, group="admin-audit"
        ):
            try:
                await record_event(app.state.session_factory, envelope)
            except Exception:
                logger.exception("Could not audit %s", envelope.event_type)

    await asyncio.gather(*(follow(t) for t in events.AUDITED))
