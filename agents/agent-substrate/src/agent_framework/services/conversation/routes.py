"""Conversation Service — HTTP routes.

Routes:
  POST   /threads              – create thread
  GET    /threads              – list threads
  GET    /threads/{id}         – get thread
  PATCH  /threads/{id}         – update thread
  DELETE /threads/{id}         – delete thread
  GET    /threads/{id}/messages – get thread messages (steps)
  POST   /threads/{id}/steps   – create step
  GET    /threads/{id}/steps/{sid} – get step
  POST   /threads/{id}/feedback – submit feedback
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agent_framework.services.conversation.service import (
    create_feedback,
    create_step,
    create_thread,
    delete_thread,
    get_step,
    get_steps,
    get_thread,
    list_threads,
    load_messages_for_memory,
    update_step,
    update_thread,
)

router = APIRouter(tags=["conversation"])


# ── Request/Response schemas ─────────────────────────────────────────────────


class ThreadCreateBody(BaseModel):
    name: Optional[str] = "New Chat"
    user_id: Optional[str] = None
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None


class ThreadUpdateBody(BaseModel):
    name: Optional[str] = None
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None


class StepCreateBody(BaseModel):
    type: str
    name: str = ""
    parent_id: Optional[str] = None
    input: Optional[str] = None
    output: Optional[str] = None
    streaming: bool = False
    is_error: bool = False
    metadata: Optional[Dict[str, Any]] = None


class StepUpdateBody(BaseModel):
    output: Optional[str] = None
    streaming: Optional[bool] = None
    is_error: Optional[bool] = None
    metadata: Optional[Dict[str, Any]] = None


class FeedbackBody(BaseModel):
    for_id: str
    value: int
    comment: Optional[str] = None


class ThreadOut(BaseModel):
    id: str
    name: Optional[str]
    user_id: Optional[str]
    tags: Optional[List[str]]
    metadata: Optional[Dict[str, Any]]
    created_at: str
    updated_at: str
    message_count: int = 0


class StepOut(BaseModel):
    id: str
    name: str
    type: str
    thread_id: str
    parent_id: Optional[str]
    input: Optional[str]
    output: Optional[str]
    streaming: bool
    is_error: Optional[bool]
    metadata: Optional[Dict[str, Any]]
    created_at: str


class FeedbackOut(BaseModel):
    id: str
    for_id: str
    thread_id: str
    value: int
    comment: Optional[str]
    created_at: str


# ── Helper to get DB session from app state ──────────────────────────────────


async def get_db(request=Depends()):
    """Yield an async DB session from the app's session_factory."""
    from fastapi import Request

    async def _get_db(request: Request):
        async with request.app.state.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    return _get_db


# Use a proper FastAPI dependency
from fastapi import Request as _Req


async def _get_db(request: _Req):
    async with request.app.state.session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── Thread routes ────────────────────────────────────────────────────────────

thread_router = APIRouter(prefix="/threads", tags=["threads"])


@thread_router.post("", status_code=201)
async def create_thread_endpoint(
    body: ThreadCreateBody,
    db: AsyncSession = Depends(_get_db),
):
    user_uuid = uuid.UUID(body.user_id) if body.user_id else None
    thread = await create_thread(
        db,
        name=body.name or "New Chat",
        user_id=user_uuid,
        tags=body.tags,
        metadata=body.metadata,
    )
    return ThreadOut(
        id=str(thread.id),
        name=thread.name,
        user_id=str(thread.user_id) if thread.user_id else None,
        tags=thread.tags,
        metadata=thread.metadata_,
        created_at=thread.created_at.isoformat(),
        updated_at=thread.updated_at.isoformat(),
        message_count=0,
    )


@thread_router.get("")
async def list_threads_endpoint(
    limit: int = 50,
    offset: int = 0,
    user_id: Optional[str] = None,
    db: AsyncSession = Depends(_get_db),
):
    user_uuid = uuid.UUID(user_id) if user_id else None
    return await list_threads(db, user_id=user_uuid, limit=limit, offset=offset)


@thread_router.get("/{thread_id}")
async def get_thread_endpoint(
    thread_id: uuid.UUID,
    db: AsyncSession = Depends(_get_db),
):
    thread = await get_thread(db, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    return ThreadOut(
        id=str(thread.id),
        name=thread.name,
        user_id=str(thread.user_id) if thread.user_id else None,
        tags=thread.tags,
        metadata=thread.metadata_,
        created_at=thread.created_at.isoformat(),
        updated_at=thread.updated_at.isoformat(),
        message_count=0,
    )


@thread_router.patch("/{thread_id}")
async def update_thread_endpoint(
    thread_id: uuid.UUID,
    body: ThreadUpdateBody,
    db: AsyncSession = Depends(_get_db),
):
    thread = await update_thread(
        db,
        thread_id,
        name=body.name,
        tags=body.tags,
        metadata=body.metadata,
    )
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    return ThreadOut(
        id=str(thread.id),
        name=thread.name,
        user_id=str(thread.user_id) if thread.user_id else None,
        tags=thread.tags,
        metadata=thread.metadata_,
        created_at=thread.created_at.isoformat(),
        updated_at=thread.updated_at.isoformat(),
        message_count=0,
    )


@thread_router.delete("/{thread_id}", status_code=204)
async def delete_thread_endpoint(
    thread_id: uuid.UUID,
    db: AsyncSession = Depends(_get_db),
):
    deleted = await delete_thread(db, thread_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Thread not found")


# ── Step routes ──────────────────────────────────────────────────────────────


@thread_router.get("/{thread_id}/messages")
async def get_messages_endpoint(
    thread_id: uuid.UUID,
    db: AsyncSession = Depends(_get_db),
):
    """Get all user/assistant messages for a thread."""
    steps = await get_steps(
        db,
        thread_id,
        types=["user_message", "assistant_message", "tool_call", "tool_result"],
    )
    return [
        StepOut(
            id=str(s.id),
            name=s.name,
            type=s.type,
            thread_id=str(s.thread_id),
            parent_id=str(s.parent_id) if s.parent_id else None,
            input=s.input,
            output=s.output,
            streaming=s.streaming,
            is_error=s.is_error,
            metadata=s.metadata_,
            created_at=s.created_at.isoformat(),
        )
        for s in steps
    ]


@thread_router.post("/{thread_id}/steps", status_code=201)
async def create_step_endpoint(
    thread_id: uuid.UUID,
    body: StepCreateBody,
    db: AsyncSession = Depends(_get_db),
):
    """Create a step (internal — used by Workflow/Agent services)."""
    parent_uuid = uuid.UUID(body.parent_id) if body.parent_id else None
    step = await create_step(
        db,
        thread_id=thread_id,
        type=body.type,
        name=body.name,
        parent_id=parent_uuid,
        input=body.input,
        output=body.output,
        streaming=body.streaming,
        is_error=body.is_error,
        metadata=body.metadata,
    )
    return StepOut(
        id=str(step.id),
        name=step.name,
        type=step.type,
        thread_id=str(step.thread_id),
        parent_id=str(step.parent_id) if step.parent_id else None,
        input=step.input,
        output=step.output,
        streaming=step.streaming,
        is_error=step.is_error,
        metadata=step.metadata_,
        created_at=step.created_at.isoformat(),
    )


@thread_router.get("/{thread_id}/steps/{step_id}")
async def get_step_endpoint(
    thread_id: uuid.UUID,
    step_id: uuid.UUID,
    db: AsyncSession = Depends(_get_db),
):
    step = await get_step(db, step_id)
    if not step or step.thread_id != thread_id:
        raise HTTPException(status_code=404, detail="Step not found")
    return StepOut(
        id=str(step.id),
        name=step.name,
        type=step.type,
        thread_id=str(step.thread_id),
        parent_id=str(step.parent_id) if step.parent_id else None,
        input=step.input,
        output=step.output,
        streaming=step.streaming,
        is_error=step.is_error,
        metadata=step.metadata_,
        created_at=step.created_at.isoformat(),
    )


@thread_router.patch("/{thread_id}/steps/{step_id}")
async def update_step_endpoint(
    thread_id: uuid.UUID,
    step_id: uuid.UUID,
    body: StepUpdateBody,
    db: AsyncSession = Depends(_get_db),
):
    step = await update_step(
        db,
        step_id,
        output=body.output,
        streaming=body.streaming,
        is_error=body.is_error,
        metadata=body.metadata,
    )
    if not step or step.thread_id != thread_id:
        raise HTTPException(status_code=404, detail="Step not found")
    return StepOut(
        id=str(step.id),
        name=step.name,
        type=step.type,
        thread_id=str(step.thread_id),
        parent_id=str(step.parent_id) if step.parent_id else None,
        input=step.input,
        output=step.output,
        streaming=step.streaming,
        is_error=step.is_error,
        metadata=step.metadata_,
        created_at=step.created_at.isoformat(),
    )


# ── Feedback routes ──────────────────────────────────────────────────────────


@thread_router.post("/{thread_id}/feedback", status_code=201)
async def create_feedback_endpoint(
    thread_id: uuid.UUID,
    body: FeedbackBody,
    db: AsyncSession = Depends(_get_db),
):
    feedback = await create_feedback(
        db,
        for_id=uuid.UUID(body.for_id),
        thread_id=thread_id,
        value=body.value,
        comment=body.comment,
    )
    return FeedbackOut(
        id=str(feedback.id),
        for_id=str(feedback.for_id),
        thread_id=str(feedback.thread_id),
        value=feedback.value,
        comment=feedback.comment,
        created_at=feedback.created_at.isoformat(),
    )


# ── Memory endpoint (internal, for Agent Runtime) ────────────────────────────

memory_router = APIRouter(prefix="/internal", tags=["internal"])


@memory_router.get("/threads/{thread_id}/memory")
async def get_memory_messages(
    thread_id: uuid.UUID,
    db: AsyncSession = Depends(_get_db),
):
    """Load steps formatted for agent memory reconstruction.

    Internal endpoint — called by Agent Runtime service.
    """
    return await load_messages_for_memory(db, thread_id)
