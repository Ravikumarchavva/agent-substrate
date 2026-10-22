from __future__ import annotations

from pydantic import BaseModel


class GroupFileOut(BaseModel):
    path: str
    name: str
    size_bytes: int
    modified_at: float


class GroupFileList(BaseModel):
    files: list[GroupFileOut]
