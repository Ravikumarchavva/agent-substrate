"""Pydantic request/response schemas for the chat server API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Thread / Session schemas ─────────────────────────────────────────────────

class ThreadCreate(BaseModel):
    """POST /threads – create a new thread."""
    name: Optional[str] = "New Chat"


class ThreadUpdate(BaseModel):
    """PATCH /threads/{id} – rename / update metadata."""
    name: Optional[str] = None
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None


class ThreadOut(BaseModel):
    """Thread response object."""
    id: uuid.UUID
    name: Optional[str]
    user_id: Optional[uuid.UUID] = None
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime
    message_count: int = 0

    model_config = {"from_attributes": True}


# ── Step / Message schemas ───────────────────────────────────────────────────

class StepOut(BaseModel):
    """Step (message / tool call) response object."""
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


# ── Chat schemas ─────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    """Single message in a chat request."""
    role: str = "user"
    content: str


class ChatRequest(BaseModel):
    """POST /chat – send a message."""
    thread_id: uuid.UUID
    messages: List[ChatMessage]


# ── Feedback schemas ─────────────────────────────────────────────────────────

class FeedbackCreate(BaseModel):
    """POST /feedbacks – create feedback on a step."""
    for_id: uuid.UUID
    thread_id: uuid.UUID
    value: int = Field(..., ge=-1, le=1)  # -1 = bad, 0 = neutral, 1 = good
    comment: Optional[str] = None


class FeedbackOut(BaseModel):
    """Feedback response object."""
    id: uuid.UUID
    for_id: uuid.UUID
    thread_id: uuid.UUID
    value: int
    comment: Optional[str] = None

    model_config = {"from_attributes": True}


# ── HITL schemas ─────────────────────────────────────────────────────────────

class HITLResponse(BaseModel):
    """POST /chat/respond/{request_id} – resolve a pending HITL request."""
    # For tool approval
    action: Optional[str] = None          # "approve" | "deny" | "modify"
    modified_arguments: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None
    # For human input
    selected_key: Optional[str] = None
    selected_label: Optional[str] = None
    freeform_text: Optional[str] = None


# ── MCP App schemas ──────────────────────────────────────────────────────────

class McpContextUpdate(BaseModel):
    """POST /threads/{id}/mcp-context – update model context from MCP App."""
    tool_name: str
    context: Any  # Structured data from the app (e.g., current board state)


# ── Element schemas ──────────────────────────────────────────────────────────

class ElementOut(BaseModel):
    """Element (attachment) response object."""
    id: uuid.UUID
    thread_id: Optional[uuid.UUID] = None
    type: Optional[str] = None
    name: str
    mime: Optional[str] = None
    size: Optional[str] = None
    display: Optional[str] = None
    url: Optional[str] = None
    for_id: Optional[uuid.UUID] = None
    props: Optional[Dict[str, Any]] = None

    model_config = {"from_attributes": True}


# ── User schemas ─────────────────────────────────────────────────────────────

class UserOut(BaseModel):
    """User response object."""
    id: uuid.UUID
    identifier: str
    metadata: Optional[Dict[str, Any]] = None
    created_at: datetime

    model_config = {"from_attributes": True}
