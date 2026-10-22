from __future__ import annotations

from pydantic import BaseModel


class TokenPush(BaseModel):
    project_id: str
    access_token: str
    refresh_token: str | None = None
    expires_at: int
    """Epoch seconds the access token expires."""


class TokenDelete(BaseModel):
    project_id: str


class Status(BaseModel):
    status: str = "ok"
