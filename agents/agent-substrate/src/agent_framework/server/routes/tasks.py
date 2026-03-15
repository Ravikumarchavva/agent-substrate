"""Tasks REST API — CRUD for the agent-driven Kanban task board."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from agent_framework.runtime.tasks.store import GlobalTaskStore
from agent_framework.runtime.hitl import WebHITLBridge

router = APIRouter(prefix="/tasks", tags=["tasks"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class TaskUpdateRequest(BaseModel):
    status: Optional[str] = None   # "todo" | "in_progress" | "done"
    title: Optional[str] = None


class AddTasksRequest(BaseModel):
    tasks: List[str]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/{conversation_id}")
async def get_tasks(conversation_id: str):
    """Return the active task list for a conversation (or null if none)."""
    store = GlobalTaskStore.get()
    task_list = store.get_by_conversation(conversation_id)
    return {"task_list": task_list.to_dict() if task_list else None}


@router.patch("/{task_list_id}/{task_id}")
async def update_task(
    task_list_id: str,
    task_id: str,
    req: TaskUpdateRequest,
    request: Request,
):
    """Update a task's status or title (drag-drop / inline edit from frontend)."""
    store = GlobalTaskStore.get()
    bridge: WebHITLBridge = request.app.state.bridge

    result = None
    if req.status:
        result = store.update_status(task_list_id, task_id, req.status)
    if req.title:
        result = store.update_task_title(task_list_id, task_id, req.title)

    if not result:
        return {"status": "error", "detail": "Task not found"}

    await bridge.put_event({
        "type": "task_updated",
        "task_list_id": task_list_id,
        "task": {
            "id": result.id,
            "title": result.title,
            "status": result.status,
            "order": result.order,
        },
    })
    return {
        "status": "ok",
        "task": {"id": result.id, "title": result.title, "status": result.status},
    }


@router.post("/{task_list_id}/tasks")
async def add_tasks(
    task_list_id: str,
    req: AddTasksRequest,
    request: Request,
):
    """Append new tasks to an existing task list (user-initiated)."""
    store = GlobalTaskStore.get()
    bridge: WebHITLBridge = request.app.state.bridge

    new_tasks = store.add_tasks(task_list_id, req.tasks)
    for t in new_tasks:
        await bridge.put_event({
            "type": "task_added",
            "task_list_id": task_list_id,
            "task": {"id": t.id, "title": t.title, "status": t.status, "order": t.order},
        })
    return {"status": "ok", "added": len(new_tasks)}


@router.delete("/{task_list_id}/{task_id}")
async def delete_task(
    task_list_id: str,
    task_id: str,
    request: Request,
):
    """Delete a task (user-initiated)."""
    store = GlobalTaskStore.get()
    bridge: WebHITLBridge = request.app.state.bridge

    deleted = store.delete_task(task_list_id, task_id)
    if not deleted:
        return {"status": "error", "detail": "Task not found"}

    await bridge.put_event({
        "type": "task_deleted",
        "task_list_id": task_list_id,
        "task_id": task_id,
    })
    return {"status": "ok"}
