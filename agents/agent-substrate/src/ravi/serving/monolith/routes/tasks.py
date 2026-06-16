"""Tasks REST API — CRUD for the agent-driven Kanban task board."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ravi.agents.storage.tasks import GlobalTaskStore
from ravi.serving.monolith.security.deps import get_current_user

router = APIRouter(
    prefix="/tasks",
    tags=["tasks"],
    dependencies=[Depends(get_current_user)],
)


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
    task_list = await store.get_by_conversation(conversation_id)
    return {"task_list": task_list.to_dict() if task_list else None}


# Note: user-initiated edits to the kanban now happen inside the MCP-App iframe,
# which renders state from the tool result's structured_content and persists
# changes via POST /threads/{id}/mcp-context (ui/update-model-context). These
# REST endpoints mutate the shared store only — they no longer push SSE bridge
# events (the bespoke task wire path was removed with the narrow-waist rebuild).


@router.patch("/{task_list_id}/{task_id}")
async def update_task(
    task_list_id: str,
    task_id: str,
    req: TaskUpdateRequest,
):
    """Update a task's status or title."""
    store = GlobalTaskStore.get()

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
async def add_tasks(task_list_id: str, req: AddTasksRequest):
    """Append new tasks to an existing task list."""
    store = GlobalTaskStore.get()
    new_tasks = await store.add_tasks(task_list_id, req.tasks)
    return {"status": "ok", "added": len(new_tasks)}


@router.delete("/{task_list_id}/{task_id}")
async def delete_task(task_list_id: str, task_id: str):
    """Delete a task."""
    store = GlobalTaskStore.get()
    deleted = await store.delete_task(task_list_id, task_id)
    if not deleted:
        return {"status": "error", "detail": "Task not found"}
    return {"status": "ok"}
