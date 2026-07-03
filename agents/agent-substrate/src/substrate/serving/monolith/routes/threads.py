"""Thread (session) CRUD endpoints.

Routes:
  POST   /threads              – create thread
  GET    /threads              – list threads
  GET    /threads/{id}         – get thread
  PATCH  /threads/{id}         – update thread
  DELETE /threads/{id}         – delete thread
  GET    /threads/{id}/messages – get thread messages
"""

from __future__ import annotations

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.serving.monolith.dependencies import ServerDependencies, get_ctx
from substrate.serving.monolith.database import get_db
from substrate.serving.monolith.schemas import (
    StepOut,
    ThreadCreate,
    ThreadOut,
    ThreadUpdate,
)
from substrate.serving.monolith.security.deps import AuthClaims, get_current_user
from substrate.serving.monolith.services import (
    create_thread,
    delete_thread,
    get_owned_thread,
    get_steps,
    list_threads,
    update_thread,
)

router = APIRouter(
    prefix="/threads",
    tags=["threads"],
)


@router.post("", response_model=ThreadOut, status_code=201)
async def create_thread_endpoint(
    body: ThreadCreate,
    db: AsyncSession = Depends(get_db),
    user: AuthClaims = Depends(get_current_user),
):
    """Create a new chat thread owned by the caller."""
    thread = await create_thread(
        db,
        name=body.name or "New Chat",
        user_identifier=user.sub,
        tenant_id=user.tenant_id,
    )
    return ThreadOut(
        id=thread.id,
        name=thread.name,
        user_id=thread.user_id,
        tags=thread.tags,
        metadata=thread.metadata_,
        created_at=thread.created_at,
        updated_at=thread.updated_at,
        message_count=0,
    )


@router.get("", response_model=List[ThreadOut])
async def list_threads_endpoint(
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    user: AuthClaims = Depends(get_current_user),
):
    """List the caller's threads, newest first."""
    rows = await list_threads(
        db,
        user_identifier=None if user.is_admin else user.sub,
        limit=limit,
        offset=offset,
    )
    return [ThreadOut(**row) for row in rows]


@router.get("/{thread_id}", response_model=ThreadOut)
async def get_thread_endpoint(
    thread_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthClaims = Depends(get_current_user),
):
    """Get a single thread by ID."""
    thread = await get_owned_thread(db, thread_id, user)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    return ThreadOut(
        id=thread.id,
        name=thread.name,
        user_id=thread.user_id,
        tags=thread.tags,
        metadata=thread.metadata_,
        created_at=thread.created_at,
        updated_at=thread.updated_at,
        message_count=0,
    )


@router.patch("/{thread_id}", response_model=ThreadOut)
async def update_thread_endpoint(
    thread_id: uuid.UUID,
    body: ThreadUpdate,
    db: AsyncSession = Depends(get_db),
    user: AuthClaims = Depends(get_current_user),
):
    """Update thread name, tags, or metadata."""
    if not await get_owned_thread(db, thread_id, user):
        raise HTTPException(status_code=404, detail="Thread not found")
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
        id=thread.id,
        name=thread.name,
        user_id=thread.user_id,
        tags=thread.tags,
        metadata=thread.metadata_,
        created_at=thread.created_at,
        updated_at=thread.updated_at,
        message_count=0,
    )


@router.delete("/{thread_id}", status_code=204)
async def delete_thread_endpoint(
    thread_id: uuid.UUID,
    ctx: ServerDependencies = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
    user: AuthClaims = Depends(get_current_user),
):
    """Delete a thread and all its data."""
    thread = await get_owned_thread(db, thread_id, user)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    deleted = await delete_thread(db, thread_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Thread not found")


@router.get("/{thread_id}/messages", response_model=List[StepOut])
async def get_thread_messages(
    thread_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthClaims = Depends(get_current_user),
):
    """Get all messages (steps) for a thread in chronological order."""
    thread = await get_owned_thread(db, thread_id, user)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    steps = await get_steps(
        db,
        thread_id,
        types=["user_message", "assistant_message", "tool_call", "tool_result"],
    )
    return [
        StepOut(
            id=s.id,
            type=s.type,
            name=s.name,
            thread_id=s.thread_id,
            parent_id=s.parent_id,
            input=s.input,
            output=s.output,
            is_error=s.is_error,
            metadata=s.metadata_,
            generation=s.generation,
            created_at=s.created_at,
            start_time=s.start_time,
            end_time=s.end_time,
        )
        for s in steps
    ]
