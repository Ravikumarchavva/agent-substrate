from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class MemberIn(BaseModel):
    agent_id: str
    mode: str = "always"


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    members: list[MemberIn] = Field(default_factory=list)


class GroupPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)


class MemberOut(BaseModel):
    agent_id: str
    name: str = ""
    role: str = ""
    mode: str = "always"
    avatar: str | None = None


class GroupOut(BaseModel):
    id: str
    name: str
    created_at: datetime
    updated_at: datetime
    members: list[MemberOut] = Field(default_factory=list)
    last_message: str | None = None
    last_sender: str | None = None
    unread: int = 0
    avatar: str | None = None
    pinned_at: datetime | None = None


class GroupList(BaseModel):
    items: list[GroupOut]


class SendMessageIn(BaseModel):
    text: str
    attachments: list[str] = Field(default_factory=list)


class MessageReactionIn(BaseModel):
    reaction: str


class MessagePatch(BaseModel):
    text: str


class MessageOut(BaseModel):
    seq: int
    sender_id: str
    sender_name: str
    text: str
    created_at: datetime
    reactions: dict[str, list[str]] = Field(default_factory=dict)


class MessageList(BaseModel):
    items: list[MessageOut]


class FeedIn(BaseModel):
    channel: str = ""


class FeedOut(BaseModel):
    status: str = "connected"
