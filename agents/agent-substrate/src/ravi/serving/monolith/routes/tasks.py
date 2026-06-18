"""Tasks REST API — CRUD for the per-agent Kanban task boards."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from ravi.serving.monolith.security.deps import get_current_user

router = APIRouter(
    prefix="/tasks",
    tags=["tasks"],
    dependencies=[Depends(get_current_user)],
)


class TaskUpdateRequest(BaseModel):
    status: Optional[str] = None
    title: Optional[str] = None
    note: Optional[str] = None


class AddTasksRequest(BaseModel):
    tasks: List[str]


@router.get("/{conversation_id}")
async def get_tasks(conversation_id: str, request: Request):
    """Return all agent boards for a conversation."""
    store = request.app.state.task_tool.store
    boards = await store.get_boards_by_conversation(conversation_id)
    return {"boards": [b.to_dict() for b in boards]}


@router.patch("/{task_list_id}/{task_id}")
async def update_task(
    task_list_id: str,
    task_id: str,
    req: TaskUpdateRequest,
    request: Request,
):
    """Update a task's status, title, or note."""
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
async def force_retry_task(task_list_id: str, task_id: str, request: Request):
    """User override: reset retry_count to 0 and reopen failed/abandoned task."""
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
async def add_tasks(task_list_id: str, req: AddTasksRequest, request: Request):
    """Append new tasks to an existing board."""
    store = request.app.state.task_tool.store
    new_tasks = await store.add_tasks(task_list_id, req.tasks)
    return {"status": "ok", "added": len(new_tasks)}


@router.delete("/{task_list_id}/{task_id}")
async def delete_task(task_list_id: str, task_id: str, request: Request):
    """Delete a task."""
    store = request.app.state.task_tool.store
    deleted = await store.delete_task(task_list_id, task_id)
    if not deleted:
        return {"status": "error", "detail": "Task not found"}
    return {"status": "ok"}
