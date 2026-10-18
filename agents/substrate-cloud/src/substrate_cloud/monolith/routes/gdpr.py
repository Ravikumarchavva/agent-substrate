"""Internal GDPR erasure API.

This route is for the platform's service account only (client role ``erase-tenant``, ADR 0007).  The SaaS control plane owns
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


def _data_admin(ctx: ServerDependencies):
    """The administrator client of the data service, or None when files and documents are local."""
    access = ctx.file_access
    if access is None or not access.data_backed:
        return None
    if access.admin is None:
        raise HTTPException(
            status_code=503,
            detail="Erasure needs the data service administrator client (DATA_ADMIN_CLIENT_ID / DATA_ADMIN_CLIENT_SECRET)",
        )
    return access.admin


def _require_store(ctx: ServerDependencies, tenant_id: str, user_id: str = "platform"):
    if ctx.file_store is None:
        raise HTTPException(status_code=503, detail="File storage is not configured")
    if not hasattr(ctx.file_store, "delete_prefix"):
        raise HTTPException(
            status_code=501, detail="File store does not support GDPR erasure"
        )
    # Fenced to the tenant being erased: the sweep can only ever touch that tenant's subtree. In the data service the store acts as
    # `user_id`: a user's folder is deleted as that user, and the tenant's shared folders as the platform's service principal.
    return ctx.files_for(tenant_id, user_id)


@router.post("/erase-user")
async def erase_user_data(
    body: EraseUserRequest,
    request: Request,
    _: object = Depends(require_service_identity("erase-tenant")),
    db: AsyncSession = Depends(get_service_scoped_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> dict:
    summary = await erase_user(
        db,
        store=_require_store(ctx, body.tenant_id, body.user_id),
        redis=request.app.state.redis,
        tenant_id=body.tenant_id,
        user_id=body.user_id,
        cfg=settings,
        memory_store=ctx.long_term_memory,
        runtime_store=ctx.runtime.store if ctx.runtime is not None else None,
        folder=request.app.state.store,
        data_admin=_data_admin(ctx),
    )
    return summary.as_dict()


@router.post("/erase-tenant")
async def erase_tenant_data(
    body: EraseTenantRequest,
    request: Request,
    _: object = Depends(require_service_identity("erase-tenant")),
    db: AsyncSession = Depends(get_service_scoped_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> dict:
    summary = await erase_tenant(
        db,
        store=_require_store(ctx, body.tenant_id),
        redis=request.app.state.redis,
        tenant_id=body.tenant_id,
        cfg=settings,
        user_store=(
            (lambda user: ctx.files_for(body.tenant_id, user))
            if ctx.file_access is not None and ctx.file_access.data_backed
            else None
        ),
        memory_store=ctx.long_term_memory,
        runtime_store=ctx.runtime.store if ctx.runtime is not None else None,
        folder=request.app.state.store,
        data_admin=_data_admin(ctx),
    )
    return summary.as_dict()
