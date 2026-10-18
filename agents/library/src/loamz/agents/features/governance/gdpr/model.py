from __future__ import annotations

from pydantic import BaseModel


class EraseUserRequest(BaseModel):
    tenant_id: str
    user_id: str


class EraseTenantRequest(BaseModel):
    tenant_id: str


class ErasureSummary(BaseModel):
    tenant_id: str
    user_id: str | None = None
    conversations_deleted: int = 0
    objects_deleted: int = 0
    memories_deleted: int = 0
    status: str = "completed"

