"""Platform administration (admin role)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from loamz.server.agents.bootstrap.http import CallerDep, use
from loamz.server.agents.features.governance.admin.model import (
    SetQuota,
    Stats,
    StepList,
    StorageConversations,
    StorageTenants,
    ThreadList,
)
from loamz.server.agents.features.governance.admin.service import AdminService, admin

Service = Annotated[AdminService, Depends(use(admin))]
PageSize = Annotated[int, Query(ge=1, le=200)]

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get("/stats", response_model=Stats)
async def admin_stats(caller: CallerDep, service: Service) -> Stats:
    return await service.stats(caller)


@router.get("/threads", response_model=ThreadList)
async def list_all_threads(
    caller: CallerDep, service: Service, page_size: PageSize = 50, page_token: str | None = None
) -> ThreadList:
    return await service.threads(caller, page_size, page_token)


@router.get("/threads/{thread_id}/steps", response_model=StepList)
async def get_thread_steps(
    thread_id: str,
    caller: CallerDep,
    service: Service,
    page_size: PageSize = 50,
    page_token: str | None = None,
) -> StepList:
    return await service.steps(caller, thread_id, page_size, page_token)


@router.delete("/threads/{thread_id}", status_code=204)
async def delete_thread(thread_id: str, caller: CallerDep, service: Service) -> Response:
    await service.delete_thread(caller, thread_id)
    return Response(status_code=204)


@router.get("/storage", response_model=StorageTenants)
async def list_storage_tenants(caller: CallerDep, service: Service) -> StorageTenants:
    return await service.storage_tenants(caller)


@router.get("/storage/{tenant_id}/conversations", response_model=StorageConversations)
async def list_storage_conversations(
    tenant_id: str,
    caller: CallerDep,
    service: Service,
    page_size: PageSize = 50,
    page_token: str | None = None,
) -> StorageConversations:
    return await service.storage_conversations(caller, tenant_id, page_size, page_token)


@router.put("/storage/{tenant_id}/quota")
async def set_storage_quota(
    tenant_id: str, body: SetQuota, caller: CallerDep, service: Service
) -> dict[str, object]:
    return await service.set_storage_quota(caller, tenant_id, body.quota_bytes)
