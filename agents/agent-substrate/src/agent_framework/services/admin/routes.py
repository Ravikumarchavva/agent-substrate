"""Admin Control Plane — HTTP routes.

Routes:
  GET  /admin/stats           – platform statistics
  GET  /admin/audit           – audit log entries
  POST /admin/tenants         – create tenant
  GET  /admin/tenants         – list tenants
  GET  /admin/tenants/{id}    – get tenant
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agent_framework.services.admin.service import (
    create_tenant,
    get_audit_logs,
    get_platform_stats,
    get_tenant,
    list_tenants,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


# ── Schemas ──────────────────────────────────────────────────────────────────


class TenantCreateBody(BaseModel):
    name: str
    slug: str
    plan: str = "free"
    settings: Optional[Dict[str, Any]] = None


class TenantOut(BaseModel):
    id: str
    name: str
    slug: str
    plan: str
    is_active: bool
    settings: Optional[Dict[str, Any]]
    created_at: str


class AuditLogOut(BaseModel):
    id: str
    tenant_id: Optional[str]
    actor_id: Optional[str]
    action: str
    resource_type: Optional[str]
    resource_id: Optional[str]
    details: Optional[Dict[str, Any]]
    created_at: str


# ── Dependencies ─────────────────────────────────────────────────────────────


async def _get_db(request: Request):
    async with request.app.state.session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── Routes ───────────────────────────────────────────────────────────────────


@router.get("/stats")
async def platform_stats(db: AsyncSession = Depends(_get_db)):
    """Get platform-wide statistics."""
    return await get_platform_stats(db)


@router.get("/audit")
async def audit_log(
    tenant_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    action: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(_get_db),
):
    """Query the audit log."""
    logs = await get_audit_logs(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        action=action,
        limit=limit,
        offset=offset,
    )
    return [
        AuditLogOut(
            id=str(log.id),
            tenant_id=log.tenant_id,
            actor_id=log.actor_id,
            action=log.action,
            resource_type=log.resource_type,
            resource_id=log.resource_id,
            details=log.details,
            created_at=log.created_at.isoformat(),
        )
        for log in logs
    ]


@router.post("/tenants", status_code=201)
async def create_tenant_endpoint(
    body: TenantCreateBody,
    db: AsyncSession = Depends(_get_db),
):
    """Create a new tenant."""
    tenant = await create_tenant(
        db,
        name=body.name,
        slug=body.slug,
        plan=body.plan,
        settings=body.settings,
    )
    return TenantOut(
        id=str(tenant.id),
        name=tenant.name,
        slug=tenant.slug,
        plan=tenant.plan,
        is_active=tenant.is_active,
        settings=tenant.settings,
        created_at=tenant.created_at.isoformat(),
    )


@router.get("/tenants")
async def list_tenants_endpoint(
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(_get_db),
):
    tenants = await list_tenants(db, limit=limit, offset=offset)
    return [
        TenantOut(
            id=str(t.id),
            name=t.name,
            slug=t.slug,
            plan=t.plan,
            is_active=t.is_active,
            settings=t.settings,
            created_at=t.created_at.isoformat(),
        )
        for t in tenants
    ]


@router.get("/tenants/{tenant_id}")
async def get_tenant_endpoint(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(_get_db),
):
    tenant = await get_tenant(db, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return TenantOut(
        id=str(tenant.id),
        name=tenant.name,
        slug=tenant.slug,
        plan=tenant.plan,
        is_active=tenant.is_active,
        settings=tenant.settings,
        created_at=tenant.created_at.isoformat(),
    )
