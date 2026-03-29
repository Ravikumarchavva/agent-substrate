"""Tasks REST API — CRUD for the agent-driven Kanban task board."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from raavan.shared.tasks.store import GlobalTaskStore
from raavan.server.context import ServerContext, get_ctx

router = APIRouter(prefix="/tasks", tags=["tasks"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TaskUpdateRequest(BaseModel):
    status: Optional[str] = None  # "todo" | "in_progress" | "done"
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
    ctx: ServerContext = Depends(get_ctx),
):
    """Update a task's status or title (drag-drop / inline edit from frontend)."""
    store = GlobalTaskStore.get()
    bridge_registry = ctx.bridge_registry

    result = None
    if req.status:
        result = await store.update_status(task_list_id, task_id, req.status)
    if req.title:
        result = await store.update_task_title(task_list_id, task_id, req.title)

    if not result:
        return {"status": "error", "detail": "Task not found"}

    # Emit to the correct per-thread bridge (looks up conversation_id from store)
    task_list_obj = store.get_task_list(task_list_id)
    if task_list_obj:
        await bridge_registry.emit(
            task_list_obj.conversation_id,
            {
                "type": "task_updated",
                "task_list_id": task_list_id,
                "task": {
                    "id": result.id,
                    "title": result.title,
                    "status": result.status,
                    "order": result.order,
                },
            },
        )
    return {
        "status": "ok",
        "task": {"id": result.id, "title": result.title, "status": result.status},
    }


@router.post("/{task_list_id}/tasks")
async def add_tasks(
    task_list_id: str,
    req: AddTasksRequest,
    ctx: ServerContext = Depends(get_ctx),
):
    """Append new tasks to an existing task list (user-initiated)."""
    store = GlobalTaskStore.get()
    bridge_registry = ctx.bridge_registry

    new_tasks = await store.add_tasks(task_list_id, req.tasks)
    task_list_obj = store.get_task_list(task_list_id)
    if task_list_obj:
        for t in new_tasks:
            await bridge_registry.emit(
                task_list_obj.conversation_id,
                {
                    "type": "task_added",
                    "task_list_id": task_list_id,
                    "task": {
                        "id": t.id,
                        "title": t.title,
                        "status": t.status,
                        "order": t.order,
                    },
                },
            )
    return {"status": "ok", "added": len(new_tasks)}


@router.delete("/{task_list_id}/{task_id}")
async def delete_task(
    task_list_id: str,
    task_id: str,
    ctx: ServerContext = Depends(get_ctx),
):
    """Delete a task (user-initiated)."""
    store = GlobalTaskStore.get()
    bridge_registry = ctx.bridge_registry

    deleted = await store.delete_task(task_list_id, task_id)
    if not deleted:
        return {"status": "error", "detail": "Task not found"}

    task_list_obj = store.get_task_list(task_list_id)
    if task_list_obj:
        await bridge_registry.emit(
            task_list_obj.conversation_id,
            {
                "type": "task_deleted",
                "task_list_id": task_list_id,
                "task_id": task_id,
            },
        )
    return {"status": "ok"}
