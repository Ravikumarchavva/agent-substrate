from __future__ import annotations

from pydantic import BaseModel, Field


class ShareOut(BaseModel):
    token: str


class SharedMessage(BaseModel):
    role: str
    text: str
    attachments: list[str] = Field(default_factory=list)


class SharedConversation(BaseModel):
    title: str | None = None
    messages: list[SharedMessage]
