"""Wire request bodies — the client→engine half of the protocol.

These back the POST endpoints (``/chat``, HITL responses). Like the events,
their TypeScript types are generated from this module so the request shapes
cannot drift between the two sides.
"""

from __future__ import annotations


from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Start (or continue) an agent run on a thread."""

    thread_id: str
    message: str
    file_ids: list[str] = Field(default_factory=list)
    model: str | None = None
    system_instructions: str | None = None


class ApprovalResponse(BaseModel):
    """Human's decision on a pending tool-approval request."""

    request_id: str
    approved: bool


class InputResponse(BaseModel):
    """Human's answer to a pending free-form input request."""

    request_id: str
    value: str


# Optional payload extras some clients send; accepted but not required.
class CancelRequest(BaseModel):
    thread_id: str


__all__ = [
    "ChatRequest",
    "ApprovalResponse",
    "InputResponse",
    "CancelRequest",
]
