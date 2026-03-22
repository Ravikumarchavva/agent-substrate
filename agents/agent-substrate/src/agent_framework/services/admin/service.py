"""Admin Control Plane — business logic.

Platform administration: stats aggregation, tenant management, audit logging.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_framework.services.admin.models import AuditLog, Tenant

logger = logging.getLogger(__name__)


# ── Audit Log ────────────────────────────────────────────────────────────────


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

    query = query.limit(limit).offset(offset)
    result = await db.execute(query)
    return list(result.scalars().all())


# ── Tenant Management ────────────────────────────────────────────────────────


async def create_tenant(
    db: AsyncSession,
    *,
    name: str,
    slug: str,
    plan: str = "free",
    settings: Optional[Dict[str, Any]] = None,
) -> Tenant:
    tenant = Tenant(
        name=name,
        slug=slug,
        plan=plan,
        settings=settings or {},
    )
    db.add(tenant)
    await db.flush()
    return tenant


async def get_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> Optional[Tenant]:
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    return result.scalar_one_or_none()


async def list_tenants(
    db: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
) -> List[Tenant]:
    result = await db.execute(
        select(Tenant).order_by(Tenant.created_at.desc()).limit(limit).offset(offset)
    )
    return list(result.scalars().all())


# ── Platform Stats ───────────────────────────────────────────────────────────


async def get_platform_stats(db: AsyncSession) -> Dict[str, Any]:
    """Aggregate platform statistics from available data."""
    tenant_count = await db.execute(select(func.count(Tenant.id)))
    audit_count = await db.execute(select(func.count(AuditLog.id)))

    return {
        "tenants": tenant_count.scalar() or 0,
        "audit_entries": audit_count.scalar() or 0,
    }
