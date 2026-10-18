"""The thread and feedback documents of the Agent API (identical in the monolith and through the gateway)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ThreadCreate(BaseModel):
    name: Optional[str] = "New Chat"


class ThreadUpdate(BaseModel):
    name: Optional[str] = None
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None
    pinned: Optional[bool] = None
    archived: Optional[bool] = None


class ThreadOut(BaseModel):
    id: uuid.UUID
    name: Optional[str]
    user_id: Optional[uuid.UUID] = None
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    locked_at: Optional[datetime] = None
    locked_reason: Optional[str] = None
    pinned_at: Optional[datetime] = None
    archived_at: Optional[datetime] = None
    agent_id: Optional[uuid.UUID] = None
    model_config = {"from_attributes": True}


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"] = "user"
    content: str


class ChatRequest(BaseModel):
    thread_id: uuid.UUID
    messages: List[ChatMessage] = Field(min_length=1)
    system_instructions: Optional[str] = None
    file_ids: Optional[List[uuid.UUID]] = None


class FeedbackCreate(BaseModel):
    for_id: uuid.UUID
    thread_id: uuid.UUID
    value: int = Field(..., ge=-1, le=1)  # -1 bad, 0 takes the rating back, 1 good
    comment: Optional[str] = None


class FeedbackOut(BaseModel):
    id: uuid.UUID
    for_id: uuid.UUID
    thread_id: uuid.UUID
    value: int
    comment: Optional[str] = None
    model_config = {"from_attributes": True}
