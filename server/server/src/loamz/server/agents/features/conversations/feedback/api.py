from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from loamz.server.agents.bootstrap.http import CallerDep, use
from loamz.server.agents.features.conversations.feedback.model import (
    FeedbackCreate,
    FeedbackList,
    FeedbackOut,
)
from loamz.server.agents.features.conversations.feedback.service import FeedbackService, feedback

Service = Annotated[FeedbackService, Depends(use(feedback))]

router = APIRouter(prefix="/api/v1", tags=["feedback"])


@router.post("/feedbacks", response_model=FeedbackOut | None, status_code=status.HTTP_201_CREATED)
async def submit_feedback(body: FeedbackCreate, caller: CallerDep, service: Service):
    """Rate an answer up or down with an optional comment; value 0 takes the rating back."""
    return await service.submit(caller, body)


@router.get("/threads/{thread_id}/feedback", response_model=FeedbackList)
async def thread_feedback(thread_id: str, caller: CallerDep, service: Service) -> FeedbackList:
    return await service.for_thread(caller, thread_id)
