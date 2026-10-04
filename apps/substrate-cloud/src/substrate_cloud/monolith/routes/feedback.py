"""Feedback endpoints.

POST /feedbacks               – rate an answer (a run) up or down, with an optional comment; ``0`` takes the rating back.
GET  /threads/{id}/feedback   – the ratings given in a conversation.
"""

from __future__ import annotations

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from substrate_cloud.monolith.security.rls_deps import get_tenant_scoped_db
from substrate_cloud.monolith.schemas import FeedbackCreate, FeedbackOut
from substrate_cloud.monolith.security.deps import AuthClaims, get_current_user
from substrate_cloud.monolith.services import (
    get_owned_thread,
    list_feedback,
    set_feedback,
)

router = APIRouter(tags=["feedback"], dependencies=[Depends(get_current_user)])


@router.post("/feedbacks", response_model=FeedbackOut | None, status_code=201)
async def submit_feedback(
    body: FeedbackCreate,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
):
    """Rate an answer. Only the conversation's owner can; one rating per answer, the latest wins."""
    if not await get_owned_thread(db, body.thread_id, user):
        raise HTTPException(status_code=404, detail="Thread not found")
    fb = await set_feedback(
        db,
        for_id=body.for_id,
        thread_id=body.thread_id,
        value=body.value,
        comment=body.comment,
    )
    return None if fb is None else FeedbackOut.model_validate(fb)


@router.get("/threads/{thread_id}/feedback", response_model=List[FeedbackOut])
async def thread_feedback(
    thread_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
):
    if not await get_owned_thread(db, thread_id, user):
        raise HTTPException(status_code=404, detail="Thread not found")
    return [FeedbackOut.model_validate(f) for f in await list_feedback(db, thread_id)]
