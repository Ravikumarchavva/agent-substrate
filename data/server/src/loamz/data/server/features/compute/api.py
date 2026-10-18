"""/api/v1/compute and /jobs: attached compute (attach only: the control plane never provisions it) and its jobs."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from loamz.data.server.kernel.errors import conflict, forbidden
from loamz.data.server.kernel.http import Caller, Svc
from loamz.data.server.kernel.principal import Principal

router = APIRouter(prefix="/api/v1")
FULL_NAME = r"^[a-z_][a-z0-9_]{0,63}\.[a-z_][a-z0-9_]{0,63}\.[a-z_][a-z0-9_]{0,63}$"


# ------------------------------------------------------------------ compute (attach only)
class ResourceAttach(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9 _.-]*$")
    role: Literal["sql", "jobs", "documents"]
    url: str = Field(min_length=1, max_length=2048)
    token: str | None = Field(default=None, min_length=32, max_length=512, repr=False)
    concurrency: int = Field(default=4, ge=1, le=64)
    max_queue: int = Field(default=100, ge=1, le=10000)


def admin(caller: Principal) -> None:
    if not caller.is_admin:
        raise forbidden("Only admins manage compute")


@router.get("/compute/resources", tags=["compute"])
async def resources(svc: Svc, caller: Caller):
    items = await svc.controller.resources()
    if not caller.is_admin:
        items = [r for r in items if svc.store.can_use_compute(caller, r["id"])]
    return {"resources": items}


@router.post("/compute/resources", status_code=201, tags=["compute"])
async def attach(
    body: ResourceAttach, svc: Svc, caller: Caller, request: Request, response: Response
):
    admin(caller)
    resource = await svc.controller.request("POST", "/resources", json=body.model_dump())
    # Attached compute is usable by every signed-in user unless an admin revokes it.
    svc.store.grant("compute", resource["id"], "group:account users", "USE_COMPUTE", caller.id)
    svc.store.audit(
        caller, "compute.attach", resource["id"], "success", request.state.request_id, url=body.url
    )
    response.headers["Location"] = f"/api/v1/compute/resources/{resource['id']}"
    return resource


@router.put("/compute/resources/{resource_id}", tags=["compute"])
async def update_resource(
    resource_id: str, body: ResourceAttach, svc: Svc, caller: Caller, request: Request
):
    admin(caller)
    resource = await svc.controller.request(
        "PUT", f"/resources/{resource_id}", json=body.model_dump()
    )
    svc.store.audit(caller, "compute.update", resource_id, "success", request.state.request_id)
    return resource


@router.post("/compute/resources/{resource_id}:{action}", tags=["compute"])
async def resource_action(
    resource_id: str,
    action: Literal["start", "stop", "delete"],
    svc: Svc,
    caller: Caller,
    request: Request,
):
    admin(caller)
    result = await svc.controller.request("POST", f"/resources/{resource_id}:{action}")
    svc.store.audit(caller, f"compute.{action}", resource_id, "success", request.state.request_id)
    return result


@router.get("/compute/resources/{resource_id}/events", tags=["compute"])
async def resource_events(resource_id: str, svc: Svc, caller: Caller):
    admin(caller)
    return await svc.controller.request("GET", f"/resources/{resource_id}/events")


@router.get("/jobs", tags=["compute"])
async def jobs(svc: Svc, caller: Caller, page_size: int = Query(100, ge=1, le=1000)):
    admin(caller)
    return await svc.controller.request("GET", "/jobs", params={"limit": page_size})


@router.get("/jobs/{job_id}", tags=["compute"])
async def job(job_id: str, svc: Svc, caller: Caller):
    admin(caller)
    return await svc.controller.request("GET", f"/jobs/{job_id}")


@router.post("/jobs/{job_id}:cancel", tags=["compute"])
async def cancel_job(job_id: str, svc: Svc, caller: Caller, request: Request):
    admin(caller)
    result = await svc.controller.request("POST", f"/jobs/{job_id}:cancel")
    svc.store.audit(caller, "job.cancel", job_id, "success", request.state.request_id)
    return result


__all__ = ["router", "conflict"]
