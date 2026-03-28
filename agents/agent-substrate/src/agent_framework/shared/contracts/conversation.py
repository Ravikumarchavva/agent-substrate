"""Shared API contracts for conversation service."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ThreadCreate(BaseModel):
    name: Optional[str] = "New Chat"
    tenant_id: str = "default"
    workspace_id: str = "default"


class ThreadUpdate(BaseModel):
    name: Optional[str] = None
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None


class ThreadOut(BaseModel):
    id: uuid.UUID
    name: Optional[str]
    user_id: Optional[uuid.UUID] = None
    tenant_id: str = "default"
    workspace_id: str = "default"
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    model_config = {"from_attributes": True}


class StepOut(BaseModel):
    id: uuid.UUID
    type: str
    name: str
    thread_id: uuid.UUID
    parent_id: Optional[uuid.UUID] = None
    input: Optional[str] = None
    output: Optional[str] = None
    is_error: Optional[bool] = None
    metadata: Optional[Dict[str, Any]] = None
    generation: Optional[Dict[str, Any]] = None
    created_at: datetime
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
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
    value: int = Field(..., ge=-1, le=1)
    comment: Optional[str] = None


class FeedbackOut(BaseModel):
    id: uuid.UUID
    for_id: uuid.UUID
    thread_id: uuid.UUID
    value: int
    comment: Optional[str] = None
    model_config = {"from_attributes": True}
