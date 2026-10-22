from pydantic import BaseModel


class Stats(BaseModel):
    total_threads: int
    total_events: int


class ThreadRow(BaseModel):
    id: str
    tenant_id: str
    name: str
    user_identifier: str
    created_at: str | None
    updated_at: str | None
    deleted_at: str | None
    event_count: int


class ThreadList(BaseModel):
    items: list[ThreadRow]
    next_page_token: str | None = None


class StepList(BaseModel):
    items: list[dict[str, object]]
    next_page_token: str | None = None


class StorageTenant(BaseModel):
    tenant_id: str
    used_bytes: int
    quota_bytes: int | None
    conversation_count: int


class StorageTenants(BaseModel):
    items: list[StorageTenant]


class StorageConversation(BaseModel):
    conversation_id: str
    size_bytes: int
    file_count: int


class StorageConversations(BaseModel):
    items: list[StorageConversation]
    next_page_token: str | None = None


class SetQuota(BaseModel):
    quota_bytes: int | None
    """None removes the tenant volume's quota."""
