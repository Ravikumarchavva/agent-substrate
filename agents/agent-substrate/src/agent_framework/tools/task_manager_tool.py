"""
Task Manager Tool — lets the agent create a visible Kanban task list.

The agent calls this tool when approaching complex multi-step questions:
  1. Call action="create_list" with a list of task titles BEFORE starting work.
  2. Call action="start_task"    when beginning each task.
  3. Call action="complete_task" when each task finishes.
  4. Optionally call action="add_task" / "delete_task" for dynamic changes.

Each action emits an SSE event via the injected event_emitter so the
frontend KanbanPanel updates in real-time.
"""
from __future__ import annotations

import contextvars
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from agent_framework.tools.base_tool import BaseTool, ToolResult
from agent_framework.tasks.store import GlobalTaskStore, Task, TaskList

logger = logging.getLogger(__name__)

# Type alias for the async event emitter (usually bridge.put_event)
EventEmitter = Callable[[Dict[str, Any]], Awaitable[None]]

# Per-async-task context variable — set by chat route before agent.run_stream().
# Using ContextVar means concurrent requests get their own value automatically.
current_thread_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "task_manager_thread_id", default="default"
)


class TaskManagerTool(BaseTool):
    """
    Manage a visible Kanban task list during complex agent runs.

    Actions
    -------
    create_list   – Create a brand-new task list (call first, before any work).
    start_task    – Move a task from "todo" → "in_progress".
    complete_task – Move a task from "in_progress" → "done".
    add_task      – Append new tasks to the current list.
    delete_task   – Remove a task by ID.
    update_title  – Rename a task (edit its title).

    Usage pattern
    -------------
    1. create_list with all planned steps
    2. start_task  → do the work → complete_task  (repeat per step)
    """

    def __init__(self, event_emitter: Optional[EventEmitter] = None) -> None:
        super().__init__(
            name="manage_tasks",
            description=(
                "Create and update a visible task-board for complex, multi-step work. "
                "ALWAYS call action=create_list FIRST with all planned steps. "
                "Then call start_task before each step and complete_task after. "
                "The user sees live Kanban updates: Todo → In Progress → Done."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "create_list",
                            "start_task",
                            "complete_task",
                            "fail_task",
                            "add_task",
                            "delete_task",
                            "update_title",
                        ],
                        "description": "Action to perform on the task list.",
                    },
                    "tasks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Task titles. Required for create_list and add_task."
                        ),
                    },
                    "task_id": {
                        "type": "string",
                        "description": (
                            "ID of the task to update. "
                            "If omitted for start_task / complete_task, "
                            "the first matching task is used automatically."
                        ),
                    },
                    "title": {
                        "type": "string",
                        "description": "New title for update_title action.",
                    },
                    "thread_id": {
                        "type": "string",
                        "description": "Conversation / thread ID (injected by the framework).",
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        )
        self._emit: Optional[EventEmitter] = event_emitter
        # task_list_id per conversation thread (supports concurrent requests)
        self._task_lists: Dict[str, Optional[str]] = {}  # thread_id -> task_list_id

    # ------------------------------------------------------------------
    # Public API: inject emitter after construction (used in main.py)
    # ------------------------------------------------------------------

    def set_event_emitter(self, emitter: EventEmitter) -> None:
        self._emit = emitter

    def reset(self) -> None:
        """Reset between conversations (clears the given thread_id)."""
        tid = current_thread_id.get()
        self._task_lists.pop(tid, None)

    # ------------------------------------------------------------------
    # Execute (called by the ReAct agent)
    # ------------------------------------------------------------------

    async def execute(
        self,
        action: str,
        tasks: Optional[List[str]] = None,
        task_id: Optional[str] = None,
        title: Optional[str] = None,
        thread_id: Optional[str] = None,
        **_kwargs: Any,
    ) -> ToolResult:
        store = GlobalTaskStore.get()
        # Prefer thread_id from ContextVar (set by chat route per-request),
        # fall back to tool argument, then to "default".
        conv_id = current_thread_id.get() or thread_id or "default"
        task_list_id = self._task_lists.get(conv_id)

        # ── create_list ──────────────────────────────────────────────
        if action == "create_list":
            if not tasks:
                return _err("tasks[] is required for create_list")

            task_list = store.create_task_list(conv_id, tasks)
            self._task_lists[conv_id] = task_list.id

            await self._fire({
                "type": "task_list_created",
                "task_list": task_list.to_dict(),
            })

            names = "\n".join(f"  {i+1}. {t}" for i, t in enumerate(tasks))
            return _ok(
                f"Task list created ({len(tasks)} tasks):\n{names}\n\n"
                f"Now call start_task (no task_id needed — auto-advances) "
                f"before each step, and complete_task after."
            )

        # ── shared guard: need an active task list ────────────────────
        if not task_list_id:
            # Attempt to auto-recover from conversation store
            existing = store.get_by_conversation(conv_id)
            if existing:
                task_list_id = existing.id
                self._task_lists[conv_id] = task_list_id
            else:
                return _err(
                    "No active task list. Call action=create_list first."
                )

        # ── start_task ───────────────────────────────────────────────
        if action == "start_task":
            resolved = self._resolve_task_id(task_id, "todo", store, task_list_id)
            if not resolved:
                return _err("No todo tasks left to start.")

            updated = store.update_status(task_list_id, resolved, "in_progress")
            if not updated:
                return _err(f"Task not found after resolution (id={resolved!r}).")

            await self._fire({
                "type": "task_updated",
                "task_list_id": task_list_id,
                "task": _task_dict(updated),
            })
            return _ok(f"Started: {updated.title}")

        # ── complete_task ─────────────────────────────────────────────
        if action == "complete_task":
            resolved = self._resolve_task_id(task_id, "in_progress", store, task_list_id)
            if not resolved:
                return _err("No in-progress tasks to complete.")

            updated = store.update_status(task_list_id, resolved, "done")
            if not updated:
                return _err(f"Task not found after resolution (id={resolved!r}).")

            await self._fire({
                "type": "task_updated",
                "task_list_id": task_list_id,
                "task": _task_dict(updated),
            })
            return _ok(f"Completed: {updated.title}")
        # ── fail_task ─────────────────────────────────────────────
        if action == "fail_task":
            resolved = self._resolve_task_id(task_id, "in_progress", store, task_list_id)
            if not resolved:
                # Fall back to any todo task if none is in progress
                resolved = self._resolve_task_id(task_id, "todo", store, task_list_id)
            if not resolved:
                return _err("No in-progress or todo task to mark as failed.")

            updated = store.update_status(task_list_id, resolved, "failed")
            if not updated:
                return _err(f"Task not found after resolution (id={resolved!r}).")

            await self._fire({
                "type": "task_updated",
                "task_list_id": task_list_id,
                "task": _task_dict(updated),
            })
            return _ok(f"Marked as failed: {updated.title}")
        # ── add_task ─────────────────────────────────────────────────
        if action == "add_task":
            if not tasks:
                return _err("tasks[] is required for add_task")

            new_tasks = store.add_tasks(task_list_id, tasks)
            for t in new_tasks:
                await self._fire({
                    "type": "task_added",
                    "task_list_id": task_list_id,
                    "task": _task_dict(t),
                })
            return _ok(f"Added {len(new_tasks)} task(s).")

        # ── delete_task ───────────────────────────────────────────────
        if action == "delete_task":
            if not task_id:
                return _err("task_id is required for delete_task")

            deleted = store.delete_task(task_list_id, task_id)
            if not deleted:
                return _err(f"Task {task_id!r} not found.")

            await self._fire({
                "type": "task_deleted",
                "task_list_id": task_list_id,
                "task_id": task_id,
            })
            return _ok(f"Deleted task {task_id}.")

        # ── update_title ──────────────────────────────────────────────
        if action == "update_title":
            if not task_id or not title:
                return _err("task_id and title are required for update_title")

            updated = store.update_task_title(task_list_id, task_id, title)
            if not updated:
                return _err(f"Task {task_id!r} not found.")

            await self._fire({
                "type": "task_updated",
                "task_list_id": task_list_id,
                "task": _task_dict(updated),
            })
            return _ok(f"Renamed task to: {updated.title}")

        return _err(f"Unknown action: {action!r}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _first_with_status(self, status: str, store, task_list_id: Optional[str]) -> Optional[str]:
        """Return the first task ID matching the given status."""
        if not task_list_id:
            return None
        task_list = store.get_task_list(task_list_id)
        if not task_list:
            return None
        for task in task_list.tasks:
            if task.status == status:
                return task.id
        return None

    def _resolve_task_id(
        self,
        task_id: Optional[str],
        status: str,
        store,
        task_list_id: str,
    ) -> Optional[str]:
        """Resolve a task_id flexibly so the agent rarely fails.

        Resolution order:
          1. No task_id supplied          → auto-advance (first task with matching status)
          2. Exact UUID match             → use it
          3. 1-based integer ('1','2'…)  → nth task in the matching-status list
          4. Case-insensitive title match → use that task's real id
          5. Fallback                    → auto-advance regardless of supplied value
        """
        task_list = store.get_task_list(task_list_id)
        if not task_list:
            return None

        # 1. No hint → auto-advance
        if not task_id:
            return self._first_with_status(status, store, task_list_id)

        # 2. Exact UUID
        for task in task_list.tasks:
            if task.id == task_id:
                return task.id

        # 3. 1-based integer index into the status-matching subset
        try:
            idx = int(task_id) - 1
            subset = [t for t in task_list.tasks if t.status == status]
            if 0 <= idx < len(subset):
                return subset[idx].id
            # Also try global index (agent might count all tasks)
            if 0 <= idx < len(task_list.tasks):
                return task_list.tasks[idx].id
        except (ValueError, TypeError):
            pass

        # 4. Title substring match (case-insensitive)
        needle = task_id.lower()
        for task in task_list.tasks:
            if needle in task.title.lower():
                return task.id

        # 5. Ultimate fallback: advance to the next task in given status
        return self._first_with_status(status, store, task_list_id)

    async def _fire(self, event: Dict[str, Any]) -> None:
        """Emit an SSE event to the frontend (non-blocking, swallows errors)."""
        if not self._emit:
            return
        try:
            await self._emit(event)
        except Exception as exc:
            logger.debug("Task event emit failed: %s", exc)


# ---------------------------------------------------------------------------
# Tiny helpers
# ---------------------------------------------------------------------------

def _ok(message: str) -> ToolResult:
    return ToolResult(
        content=[{"type": "text", "text": message}],
        isError=False,
    )


def _err(message: str) -> ToolResult:
    return ToolResult(
        content=[{"type": "text", "text": message}],
        isError=True,
    )


def _task_dict(task: Task) -> Dict[str, Any]:
    return {"id": task.id, "title": task.title, "status": task.status, "order": task.order}
