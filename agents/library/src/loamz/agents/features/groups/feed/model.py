from __future__ import annotations

from pydantic import BaseModel, Field


class FeedIn(BaseModel):
    since: dict[str, int] = Field(default_factory=dict)
    watch: str | None = None

