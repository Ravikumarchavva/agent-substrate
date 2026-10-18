from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from loamz.agents.bootstrap.http import use
from loamz.agents.features.governance.gdpr.model import (
    EraseTenantRequest,
    EraseUserRequest,
    ErasureSummary,
)
from loamz.agents.features.governance.gdpr.service import GdprService, gdpr

Service = Annotated[GdprService, Depends(use(gdpr))]

router = APIRouter(prefix="/api/v1/internal/gdpr", tags=["gdpr"])


@router.post("/erase-user", response_model=ErasureSummary)
async def erase_user_data(body: EraseUserRequest, service: Service) -> ErasureSummary:
    return await service.erase_user(body.tenant_id, body.user_id)


@router.post("/erase-tenant", response_model=ErasureSummary)
async def erase_tenant_data(body: EraseTenantRequest, service: Service) -> ErasureSummary:
    return await service.erase_tenant(body.tenant_id)

