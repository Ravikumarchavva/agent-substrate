"""Tasks REST API — CRUD for the agent-driven Kanban task board."""

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
    status: Optional[str] = None  # "todo" | "in_progress" | "done"
    title: Optional[str] = None


class AddTasksRequest(BaseModel):
    tasks: List[str]


@router.get("/{conversation_id}")
async def get_tasks(conversation_id: str, request: Request):
    """Return the active task list for a conversation (or null if none)."""
    store = request.app.state.task_tool.store
    task_list = await store.get_by_conversation(conversation_id)
    return {"task_list": task_list.to_dict() if task_list else None}


@router.patch("/{task_list_id}/{task_id}")
async def update_task(
    task_list_id: str,
    task_id: str,
    req: TaskUpdateRequest,
    request: Request,
):
    """Update a task's status or title."""
    store = request.app.state.task_tool.store

    result = None
    if req.status:
        result = await store.update_status(task_list_id, task_id, req.status)
    if req.title:
        result = await store.update_task_title(task_list_id, task_id, req.title)

    if not result:
        return {"status": "error", "detail": "Task not found"}

    return {
        "status": "ok",
        "task": {"id": result.id, "title": result.title, "status": result.status},
    }


@router.post("/{task_list_id}/tasks")
async def add_tasks(task_list_id: str, req: AddTasksRequest, request: Request):
    """Append new tasks to an existing task list."""
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
