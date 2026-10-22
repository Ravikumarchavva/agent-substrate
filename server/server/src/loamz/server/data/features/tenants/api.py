from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from starlette.concurrency import run_in_threadpool

from loamz.server.data.features.tenants.model import Tenant, TenantEnsure
from loamz.server.data.features.tenants.service import TenantsService, tenants
from loamz.server.data.kernel.http import Caller, use

router = APIRouter(prefix="/api/v1", tags=["tenants"])
Service = Annotated[TenantsService, Depends(use(tenants))]


@router.post("/tenants", response_model=Tenant)
async def ensure_tenant(
    body: TenantEnsure, caller: Caller, service: Service, request: Request, response: Response
) -> Tenant:
    tenant = await run_in_threadpool(
        service.ensure, caller, body.tenant_id, request.state.request_id
    )
    if tenant.created:
        response.status_code = 201
    return tenant
