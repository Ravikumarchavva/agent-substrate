"""Entitlements API: ``GET /api/v1/entitlements`` for the caller's tenant; ``PUT /api/v1/tenants/{tenant_id}/entitlements`` for the platform.

The platform configures entitlements through cell-admin: the realm service account holding the ``provision-tenant`` client role
(or a ``platform_admin`` user). A tenant can read its own entitlements and never change them.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from substrate_identity import Forbidden

from substrate_cloud.monolith.security.deps import AuthClaims, get_current_user
from substrate_cloud.monolith.security.rls_deps import (
    get_service_scoped_db,
    get_tenant_scoped_db,
)
from substrate_cloud.monolith.services.entitlements import (
    get_entitlements,
    set_entitlements,
)

router = APIRouter(tags=["entitlements"])


class EntitlementsOut(BaseModel):
    tenant_id: str
    daily_message_limit: int | None = Field(
        None, description="Messages per day for the whole tenant; null means unlimited"
    )
    features: dict[str, Any] = Field(default_factory=dict)


class EntitlementsIn(BaseModel):
    daily_message_limit: int | None = Field(None, ge=0)
    features: dict[str, Any] = Field(default_factory=dict)


def require_cell_admin(
    request: Request, user: AuthClaims = Depends(get_current_user)
) -> AuthClaims:
    """A platform administrator, or the cell-admin service account."""
    if user.role == "platform_admin" or (
        user.is_service and request.state.identity.has_role("provision-tenant")
    ):
        return user
    raise Forbidden("Only the platform (cell-admin) may change entitlements")


@router.get("/entitlements", response_model=EntitlementsOut)
async def my_entitlements(
    user: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> EntitlementsOut:
    found = await get_entitlements(db, user.tenant_id)
    return EntitlementsOut(
        tenant_id=found.tenant_id,
        daily_message_limit=found.daily_message_limit,
        features=found.features,
    )


@router.put("/tenants/{tenant_id}/entitlements", response_model=EntitlementsOut)
async def put_entitlements(
    tenant_id: str,
    body: EntitlementsIn,
    admin: AuthClaims = Depends(require_cell_admin),
    db: AsyncSession = Depends(get_service_scoped_db),
) -> EntitlementsOut:
    saved = await set_entitlements(
        db,
        tenant_id,
        daily_message_limit=body.daily_message_limit,
        features=body.features,
        updated_by=admin.sub,
    )
    return EntitlementsOut(
        tenant_id=saved.tenant_id,
        daily_message_limit=saved.daily_message_limit,
        features=saved.features,
    )
