from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from loamz.agents.kernel.types.content import JsonObject


class ThreadCreate(BaseModel):
    """POST /threads: a new conversation."""

    name: str | None = "New Chat"
    agent_id: str | None = Field(
        default=None,
        description="Start the conversation with one of the caller's agents (its one conversation is opened, not added to).",
    )


class ThreadUpdate(BaseModel):
    """PATCH /threads/{id}: rename, tag, pin or archive."""

    name: str | None = None
    tags: list[str] | None = None
    metadata: JsonObject | None = None
    pinned: bool | None = None
    archived: bool | None = None


class ThreadOut(BaseModel):
    id: str
    name: str | None = None
    user_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    locked_at: datetime | None = None
    locked_reason: str | None = None
    pinned_at: datetime | None = None
    archived_at: datetime | None = None
    agent_id: str | None = None


class ThreadList(BaseModel):
    items: list[ThreadOut]
    next_page_token: str | None = None


class SearchHit(BaseModel):
    thread_id: str
    thread_name: str | None = None
    role: Literal["user", "assistant"]
    snippet: str


class SearchResults(BaseModel):
    items: list[SearchHit]


class ToolStep(BaseModel):
    call_id: str = ""
    name: str
    args: str = ""
    ok: bool | None = None
    """None: no result was journaled (the run stopped before it came back)."""
    output: str = ""
    risk: str | None = None


class RunDetail(BaseModel):
    run_id: str
    status: str
    started_at: datetime | None = None
    duration_ms: int | None = None
    model: str | None = None
    llm_calls: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    tools: list[ToolStep] = Field(default_factory=list)
    error: str | None = None
    user_message: str = ""


class RunDetails(BaseModel):
    items: list[RunDetail]


class MessageEvents(BaseModel):
    """A thread's conversation as wire events, each with when it was logged (`at`)."""

    items: list[dict]


class LastWord(BaseModel):
    text: str
    at: datetime | None = None
