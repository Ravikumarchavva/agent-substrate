"""Internal GDPR erasure API.

This route is intentionally service-token-only.  The SaaS control plane owns
the user confirmation flow; agent-substrate owns the cross-store deletion.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.capabilities.gdpr.eraser import erase_tenant, erase_user
from substrate.serving.monolith.security.rls_deps import get_service_scoped_db
from substrate.serving.monolith.dependencies import ServerDependencies, get_ctx
from substrate.serving.shared.auth.middleware import require_service_identity

router = APIRouter(prefix="/internal/gdpr", tags=["gdpr"])


class EraseUserRequest(BaseModel):
    tenant_id: str
    user_id: str


class EraseTenantRequest(BaseModel):
    tenant_id: str


def _require_store(ctx: ServerDependencies):
    if ctx.file_store is None:
        raise HTTPException(status_code=503, detail="File storage is not configured")
    if not hasattr(ctx.file_store, "delete_prefix"):
        raise HTTPException(
            status_code=501, detail="File store does not support GDPR erasure"
        )
    return ctx.file_store


@router.post("/erase-user")
async def erase_user_data(
    body: EraseUserRequest,
    request: Request,
    _: object = Depends(require_service_identity),
    db: AsyncSession = Depends(get_service_scoped_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> dict:
    summary = await erase_user(
        db,
        store=_require_store(ctx),
        redis=request.app.state.redis,
        tenant_id=body.tenant_id,
        user_id=body.user_id,
    )
    return summary.as_dict()


@router.post("/erase-tenant")
async def erase_tenant_data(
    body: EraseTenantRequest,
    request: Request,
    _: object = Depends(require_service_identity),
    db: AsyncSession = Depends(get_service_scoped_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> dict:
    summary = await erase_tenant(
        db,
        store=_require_store(ctx),
        redis=request.app.state.redis,
        tenant_id=body.tenant_id,
    )
    return summary.as_dict()
