from __future__ import annotations

from pydantic import BaseModel, Field


class Entitlements(BaseModel):
    tenant_id: str
    daily_message_limit: int | None = Field(
        default=None, description="Messages per day for the whole tenant; null means unlimited"
    )
    features: dict[str, object] = Field(default_factory=dict)


class EntitlementsIn(BaseModel):
    daily_message_limit: int | None = Field(default=None, ge=0)
    features: dict[str, object] = Field(default_factory=dict)
