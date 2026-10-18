"""Admin Control Plane: HTTP routes (platform administrators only).

GET /api/v1/admin/stats   counts of what the audit trail holds
GET /api/v1/admin/audit   audit log entries, newest first
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from substrate_cloud.services.admin.service import get_audit_logs, get_platform_stats
from substrate_cloud.shared.database.dependency import get_db_session

router = APIRouter(prefix="/admin", tags=["admin"])


class AuditLogOut(BaseModel):
    id: str
    tenant_id: Optional[str]
    actor_id: Optional[str]
    action: str
    resource_type: Optional[str]
    resource_id: Optional[str]
    details: Optional[Dict[str, Any]]
    created_at: str


@router.get("/stats")
async def platform_stats(db: AsyncSession = Depends(get_db_session)) -> dict:
    return await get_platform_stats(db)


@router.get("/audit")
async def audit_log(
    tenant_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    action: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_session),
) -> list[AuditLogOut]:
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
