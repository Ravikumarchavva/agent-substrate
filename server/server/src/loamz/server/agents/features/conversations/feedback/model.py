from __future__ import annotations

from pydantic import BaseModel, Field


class FeedbackCreate(BaseModel):
    """POST /feedbacks: rate an answer; 0 takes the rating back."""

    for_id: str
    thread_id: str
    value: int = Field(..., ge=-1, le=1)
    comment: str | None = None


class FeedbackOut(BaseModel):
    id: str
    for_id: str
    thread_id: str
    value: int
    comment: str | None = None


class FeedbackList(BaseModel):
    items: list[FeedbackOut]
