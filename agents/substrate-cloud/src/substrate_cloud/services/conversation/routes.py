"""Conversation Service: HTTP routes (internal; the gateway serves them to clients as the Agent API).

  POST   /api/v1/threads                 create a thread owned by the caller
  GET    /api/v1/threads                 the caller's threads
  GET    /api/v1/threads/{id}            one thread
  PATCH  /api/v1/threads/{id}            rename, tag, pin, archive
  DELETE /api/v1/threads/{id}            delete (hidden from the owner, kept for review)
  GET    /api/v1/threads/{id}/messages   the conversation, projected from the run journal
  POST   /api/v1/feedbacks               rate an answer
  GET    /api/v1/threads/{id}/feedback   the ratings given in a conversation

The owner and tenant of a thread are the token's, never a field of the request.
"""

from __future__ import annotations

import uuid
from typing import List

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from substrate_cloud.services.conversation import service
from substrate_cloud.services.conversation.models import Thread
from substrate_cloud.shared.auth.claims import AuthClaims
from substrate_cloud.shared.auth.middleware import get_current_user
from substrate_cloud.shared.contracts.conversation import (
    FeedbackCreate,
    FeedbackOut,
    ThreadCreate,
    ThreadOut,
    ThreadUpdate,
)
from substrate_cloud.shared.database.dependency import get_db_session
from substrate_cloud.shared.events import types as events
from substrate_cloud.shared.problem import Problem
from substrate_cloud.stream.history import project_thread_timed
from substrate_cloud.stream.search import message_counts

MAX_PAGE = 100

router = APIRouter(tags=["conversation"])
thread_router = APIRouter(prefix="/threads", tags=["threads"])


async def owned(db: AsyncSession, thread_id: uuid.UUID, user: AuthClaims) -> Thread:
    thread = await service.get_owned_thread(db, thread_id, user)
    if thread is None:
        raise Problem(404, "not-found", "Thread not found")
    return thread


@thread_router.post("", response_model=ThreadOut, status_code=201)
async def create_thread_endpoint(
    body: ThreadCreate,
    request: Request,
    user: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ThreadOut:
    thread = await service.create_thread(db, user, name=body.name or "New Chat")
    await request.app.state.event_bus.publish(
        events.thread_created(
            str(thread.id),
            thread.name or "",
            tenant_id=user.tenant_id,
            user_id=user.sub,
        )
    )
    return ThreadOut(**service.thread_row(thread))


@thread_router.get("", response_model=List[ThreadOut])
async def list_threads_endpoint(
    request: Request,
    limit: int = Query(50, ge=1, le=MAX_PAGE),
    offset: int = Query(0, ge=0),
    archived: bool = False,
    user: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[ThreadOut]:
    threads = await service.list_threads(
        db, user, limit=limit, offset=offset, archived=archived
    )
    counts = (
        await message_counts(
            request.app.state.store,
            tenant=user.tenant_id,
            thread_ids=[str(t.id) for t in threads],
        )
        if threads
        else {}
    )
    return [
        ThreadOut(**service.thread_row(t, counts.get(str(t.id), 0))) for t in threads
    ]


@thread_router.get("/{thread_id}", response_model=ThreadOut)
async def get_thread_endpoint(
    thread_id: uuid.UUID,
    user: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ThreadOut:
    return ThreadOut(**service.thread_row(await owned(db, thread_id, user)))


@thread_router.patch("/{thread_id}", response_model=ThreadOut)
async def update_thread_endpoint(
    thread_id: uuid.UUID,
    body: ThreadUpdate,
    user: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ThreadOut:
    thread = await owned(db, thread_id, user)
    thread = await service.update_thread(
        db,
        thread,
        name=body.name,
        tags=body.tags,
        metadata=body.metadata,
        pinned=body.pinned,
        archived=body.archived,
    )
    return ThreadOut(**service.thread_row(thread))


@thread_router.delete("/{thread_id}", status_code=204)
async def delete_thread_endpoint(
    thread_id: uuid.UUID,
    request: Request,
    user: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    thread = await owned(db, thread_id, user)
    await service.delete_thread(db, thread)
    await request.app.state.event_bus.publish(
        events.thread_deleted(
            str(thread.id), tenant_id=thread.tenant_id, user_id=user.sub
        )
    )


@thread_router.get("/{thread_id}/messages")
async def get_thread_messages(
    thread_id: uuid.UUID,
    request: Request,
    user: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[dict]:
    """The whole conversation as wire events (``user.message``, ``text.delta``, ``tool.call``...), oldest first, each with when it
    was logged: the same projection of the run journal the monolith serves."""
    await owned(db, thread_id, user)
    timed = await project_thread_timed(request.app.state.runtime.store, str(thread_id))
    return [
        {**event.model_dump(mode="json"), "at": at.isoformat()} for event, at in timed
    ]


@thread_router.get("/{thread_id}/feedback", response_model=List[FeedbackOut])
async def thread_feedback(
    thread_id: uuid.UUID,
    user: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[FeedbackOut]:
    await owned(db, thread_id, user)
    return [
        FeedbackOut.model_validate(f)
        for f in await service.list_feedback(db, thread_id)
    ]


@router.post("/feedbacks", response_model=FeedbackOut | None, status_code=201)
async def submit_feedback(
    body: FeedbackCreate,
    user: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> FeedbackOut | None:
    """Rate an answer. Only the conversation's owner can; one rating per answer, the latest wins."""
    await owned(db, body.thread_id, user)
    saved = await service.set_feedback(
        db,
        for_id=body.for_id,
        thread_id=body.thread_id,
        value=body.value,
        comment=body.comment,
    )
    return None if saved is None else FeedbackOut.model_validate(saved)
