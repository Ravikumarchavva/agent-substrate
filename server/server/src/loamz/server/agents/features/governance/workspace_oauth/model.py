from __future__ import annotations

from pydantic import BaseModel, Field


class SetWorkspaceToken(BaseModel):
    access_token: str = Field(min_length=1)
    refresh_token: str | None = None
    """Accepted for compatibility with the sign-in flow; never stored (the frontend owns refreshing)."""
    expires_in: int = 3600


class WorkspaceToken(BaseModel):
    access_token: str
