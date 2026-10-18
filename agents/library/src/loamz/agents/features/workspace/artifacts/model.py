from __future__ import annotations

from pydantic import BaseModel, Field


class ArtifactOut(BaseModel):
    slug: str
    scope: str
    type: str
    title: str | None = None
    description: str | None = None
    body: str = ""
    tags: list[str] = Field(default_factory=list)
    status: str = "stable"
    resource: str | None = None
    trust: str = "Unverified"
    generated: dict[str, object] | None = None
    stale_after: str | None = None


class ArtifactCreate(BaseModel):
    type: str = "Memory"
    title: str | None = None
    description: str | None = None
    body: str = ""
    tags: list[str] = Field(default_factory=list)
    resource: str | None = None


class ArtifactPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    body: str | None = None
    tags: list[str] | None = None
    status: str | None = None


class ArtifactList(BaseModel):
    items: list[ArtifactOut]
