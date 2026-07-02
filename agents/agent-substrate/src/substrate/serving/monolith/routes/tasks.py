"""Tasks REST API — CRUD for the per-agent Kanban task boards.

Boards belong to a conversation (thread); every route authorizes the caller
against the owning thread before touching board state.
"""

from __future__ import annotations

import uuid
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.serving.monolith.database import get_db
from substrate.serving.monolith.security.deps import AuthClaims, get_current_user
from substrate.serving.monolith.services import get_owned_thread

router = APIRouter(
    prefix="/tasks",
    tags=["tasks"],
)


class TaskUpdateRequest(BaseModel):
    status: Optional[str] = None
    title: Optional[str] = None
    note: Optional[str] = None


class AddTasksRequest(BaseModel):
    tasks: List[str]


async def _authorize_conversation(
    conversation_id: str, db: AsyncSession, user: AuthClaims
) -> None:
    """404 unless *conversation_id* is a thread the caller owns."""
    try:
        thread_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Thread not found")
    if not await get_owned_thread(db, thread_uuid, user):
        raise HTTPException(status_code=404, detail="Thread not found")


async def _authorize_board(
    task_list_id: str, request: Request, db: AsyncSession, user: AuthClaims
) -> Any:
    """Return the board if its owning conversation belongs to the caller, else 404."""
    store = request.app.state.task_tool.store
    board = await store.get_task_list(task_list_id)
    if board is None:
        raise HTTPException(status_code=404, detail="Task list not found")
    await _authorize_conversation(board.conversation_id, db, user)
    return board


@router.get("/{conversation_id}")
async def get_tasks(
    conversation_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: AuthClaims = Depends(get_current_user),
):
    """Return all agent boards for a conversation."""
    await _authorize_conversation(conversation_id, db, user)
    store = request.app.state.task_tool.store
    boards = await store.get_boards_by_conversation(conversation_id)
    return {"boards": [b.to_dict() for b in boards]}


@router.patch("/{task_list_id}/{task_id}")
async def update_task(
    task_list_id: str,
    task_id: str,
    req: TaskUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: AuthClaims = Depends(get_current_user),
):
    """Update a task's status, title, or note."""
    await _authorize_board(task_list_id, request, db, user)
    store = request.app.state.task_tool.store

    result = None
    if req.status:
        result = await store.update_status(
            task_list_id, task_id, req.status, note=req.note or ""
        )
    if req.title:
        result = await store.update_task_title(task_list_id, task_id, req.title)

    if not result:
        return {"status": "error", "detail": "Task not found"}

    return {
        "status": "ok",
        "task": {
            "id": result.id,
            "title": result.title,
            "status": result.status,
            "note": result.note,
        },
    }


@router.post("/{task_list_id}/{task_id}/retry")
async def force_retry_task(
    task_list_id: str,
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: AuthClaims = Depends(get_current_user),
):
    """User override: reset retry_count to 0 and reopen failed/abandoned task."""
    await _authorize_board(task_list_id, request, db, user)
    store = request.app.state.task_tool.store
    result = await store.force_retry(task_list_id, task_id)
    if not result:
        return {"status": "error", "detail": "Task not found"}
    return {
        "status": "ok",
        "task": {
            "id": result.id,
            "title": result.title,
            "status": result.status,
            "retry_count": result.retry_count,
        },
    }


@router.post("/{task_list_id}/tasks")
async def add_tasks(
    task_list_id: str,
    req: AddTasksRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: AuthClaims = Depends(get_current_user),
):
    """Append new tasks to an existing board."""
    await _authorize_board(task_list_id, request, db, user)
    store = request.app.state.task_tool.store
    new_tasks = await store.add_tasks(task_list_id, req.tasks)
    return {"status": "ok", "added": len(new_tasks)}


@router.delete("/{task_list_id}/{task_id}")
async def delete_task(
    task_list_id: str,
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: AuthClaims = Depends(get_current_user),
):
    """Delete a task."""
    await _authorize_board(task_list_id, request, db, user)
    store = request.app.state.task_tool.store
    deleted = await store.delete_task(task_list_id, task_id)
    if not deleted:
        return {"status": "error", "detail": "Task not found"}
    return {"status": "ok"}
