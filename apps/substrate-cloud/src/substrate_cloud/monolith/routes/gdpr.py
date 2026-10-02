"""Internal GDPR erasure API.

This route is intentionally service-token-only.  The SaaS control plane owns
the user confirmation flow; agent-substrate owns the cross-store deletion.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from substrate_cloud.gdpr.eraser import erase_tenant, erase_user
from substrate_cloud.monolith.security.rls_deps import get_service_scoped_db
from substrate_cloud.monolith.dependencies import ServerDependencies, get_ctx
from substrate_cloud.shared.auth.middleware import require_service_identity
from substrate_cloud.shared.settings import settings

router = APIRouter(prefix="/internal/gdpr", tags=["gdpr"])


class EraseUserRequest(BaseModel):
    tenant_id: str
    user_id: str


class EraseTenantRequest(BaseModel):
    tenant_id: str


def _require_store(ctx: ServerDependencies, tenant_id: str):
    if ctx.file_store is None:
        raise HTTPException(status_code=503, detail="File storage is not configured")
    if not hasattr(ctx.file_store, "delete_prefix"):
        raise HTTPException(
            status_code=501, detail="File store does not support GDPR erasure"
        )
    # Fenced to the tenant being erased: the sweep can only ever touch that tenant's subtree.
    return ctx.files_for(tenant_id)


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
        store=_require_store(ctx, body.tenant_id),
        redis=request.app.state.redis,
        tenant_id=body.tenant_id,
        user_id=body.user_id,
        cfg=settings,
        pending_store=ctx.pending_for(body.tenant_id),
        memory_store=ctx.long_term_memory,
        runtime_store=ctx.runtime.store if ctx.runtime is not None else None,
        folder=request.app.state.store,
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
        store=_require_store(ctx, body.tenant_id),
        redis=request.app.state.redis,
        tenant_id=body.tenant_id,
        cfg=settings,
        pending_store=ctx.pending_for(body.tenant_id),
        memory_store=ctx.long_term_memory,
        runtime_store=ctx.runtime.store if ctx.runtime is not None else None,
        folder=request.app.state.store,
    )
    return summary.as_dict()
