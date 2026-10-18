"""Attached compute resources and durable batch-job requests."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from loamz.data.server.features.compute.manifests import Operation
from loamz.data.server.kernel.settings import validate_service_url

Role = Literal["sql", "jobs", "documents"]
TERMINAL = {"succeeded", "failed", "cancelled"}
KIND_FOR_ROLE = {
    "sql": "sql_warehouse",
    "jobs": "all_purpose",
    "documents": "documents",
}


def resource_kind(resource: dict) -> str:
    return resource.get("kind") or KIND_FOR_ROLE[resource["role"]]


class ResourceSpec(BaseModel):
    """An externally operated compute endpoint attached to the control plane.

    The control plane stores the address and a connection secret. It never provisions,
    sizes, starts or terminates the compute behind it.
    """

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9 _.-]*$")
    role: Role
    url: str = Field(min_length=1, max_length=2048)
    # Write-only: stored privately by the controller and never returned by any API.
    token: str | None = Field(default=None, min_length=32, max_length=512, repr=False)
    concurrency: int = Field(default=4, ge=1, le=64)
    max_queue: int = Field(default=100, ge=1, le=10000)

    @model_validator(mode="after")
    def attachment(self) -> ResourceSpec:
        self.url = validate_service_url(self.url)
        return self


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Operation
    payload: dict
    resource_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    max_attempts: int = Field(default=2, ge=1, le=5)
    queue_timeout_seconds: int = Field(default=600, ge=1, le=86400)
    timeout_seconds: float | None = Field(default=None, gt=0, le=24 * 3600)
