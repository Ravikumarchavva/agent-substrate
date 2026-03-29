"""Feedback endpoint.

POST /feedbacks – submit feedback on a message step.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from raavan.server.database import get_db
from raavan.server.schemas import FeedbackCreate, FeedbackOut
from raavan.server.services import create_feedback

router = APIRouter(tags=["feedback"])


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
