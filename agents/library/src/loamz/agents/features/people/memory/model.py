from __future__ import annotations

from pydantic import BaseModel, Field

MAX_MEMORIES = 100
"""What one person may keep. The list shows this many, so the cap keeps every memory visible (and deletable)."""

MAX_MEMORY_CHARS = 500


class MemoryOut(BaseModel):
    id: str
    content: str


class MemoryList(BaseModel):
    items: list[MemoryOut]


class MemoryIn(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_MEMORY_CHARS)
