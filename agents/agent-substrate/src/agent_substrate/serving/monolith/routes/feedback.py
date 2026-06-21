"""Feedback endpoint.

POST /feedbacks – submit feedback on a message step.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from agent_substrate.serving.monolith.database import get_db
from agent_substrate.serving.monolith.schemas import FeedbackCreate, FeedbackOut
from agent_substrate.serving.monolith.security.deps import get_current_user
from agent_substrate.serving.monolith.services import create_feedback

router = APIRouter(tags=["feedback"], dependencies=[Depends(get_current_user)])


@router.post("/feedbacks", response_model=FeedbackOut, status_code=201)
async def submit_feedback(
    body: FeedbackCreate,
    db: AsyncSession = Depends(get_db),
):
    """Submit feedback (thumbs up / down) on an assistant step."""
    fb = await create_feedback(
        db,
        for_id=body.for_id,
        thread_id=body.thread_id,
        value=body.value,
        comment=body.comment,
    )
    return FeedbackOut(
        id=fb.id,
        for_id=fb.for_id,
        thread_id=fb.thread_id,
        value=fb.value,
        comment=fb.comment,
    )
