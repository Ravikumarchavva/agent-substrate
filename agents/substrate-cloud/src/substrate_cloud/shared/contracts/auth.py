"""Shared API contracts for auth-related communication between services."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel


# ── Token Exchange ───────────────────────────────────────────────────────────


class TokenExchangeRequest(BaseModel):
    frontend_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class AgentTokenRequest(BaseModel):
    thread_id: str
    permissions: list[str] | None = None


class AgentTokenResponse(BaseModel):
    agent_token: str
    token_type: str = "bearer"
    expires_in: int
    thread_id: str


# ── User Management ─────────────────────────────────────────────────────────


class UserOut(BaseModel):
    id: uuid.UUID
    identifier: str
    email: str = ""
    role: str = "end_user"
    tenant_id: str = "default"
    metadata: Optional[Dict[str, Any]] = None
    created_at: datetime
    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    identifier: str
    email: str = ""
    role: str = "end_user"
    tenant_id: str = "default"
    metadata: Optional[Dict[str, Any]] = None
