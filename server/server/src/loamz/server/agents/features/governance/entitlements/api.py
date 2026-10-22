"""Entitlements: read by the tenant, configured by the platform (cell-admin)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from loamz.server.agents.bootstrap.http import CallerDep, use
from loamz.server.agents.features.governance.entitlements.model import Entitlements, EntitlementsIn
from loamz.server.agents.features.governance.entitlements.service import (
    EntitlementsService,
    entitlements,
)

Service = Annotated[EntitlementsService, Depends(use(entitlements))]

router = APIRouter(prefix="/api/v1", tags=["entitlements"])


@router.get("/entitlements", response_model=Entitlements)
async def my_entitlements(caller: CallerDep, service: Service) -> Entitlements:
    return await service.mine(caller)


@router.put("/tenants/{tenant_id}/entitlements", response_model=Entitlements)
async def put_entitlements(
    tenant_id: str, body: EntitlementsIn, caller: CallerDep, service: Service
) -> Entitlements:
    return await service.put(caller, tenant_id, body)
