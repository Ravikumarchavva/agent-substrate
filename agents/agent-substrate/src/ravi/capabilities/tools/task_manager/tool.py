"""
Task Manager Tool — lets the agent create a visible Kanban task list.

The agent calls this tool when approaching complex multi-step questions:
  1. Call action="create_list" with a list of task titles BEFORE starting work.
  2. Call action="start_task"    when beginning each task.
  3. Call action="complete_task" when each task finishes.
  4. Optionally call action="add_task" / "delete_task" for dynamic changes.

Each action returns the full board in ``structured_content``; the agent lowers
it into a ``UIResourceBlock`` (``ui://kanban_board``) that renders as a live
Kanban via the MCP-Apps narrow waist — no bespoke task wire events.
"""

from __future__ import annotations
from ravi.logger import setup_logging

import contextvars
from typing import Any, Dict

from ravi.kernel.tools import ToolExecutionResult, ToolUI
from ravi.kernel import TextBlock

logger = setup_logging()

# Per-async-task context variable — set by chat route before agent.run_stream().
# Using ContextVar means concurrent requests get their own value automatically.
current_thread_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "task_manager_thread_id", default="default"
)


class TaskManagerTool:
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

    risk: str = "critical"  # TODO: L4-hitl  # writes task state

    # Renders through the bundled kanban MCP App (ui://kanban_board). The agent
    # lowers each result's structured_content into a UIResourceBlock.
    ui: ToolUI = ToolUI(resource_uri="ui://kanban_board", prefers_border=True)

    name: str = "manage_tasks"
    description: str = (
        "Create and update a visible task-board for complex, multi-step work. "
        "ALWAYS call action=create_list FIRST with all planned steps. "
        "Then call start_task before each step and complete_task after. "
        "Use fail_task when a step cannot be completed. "
        "When a task fails and its retry_count < max_retries, call retry_task "
        "to increment the counter and move it back to in_progress. "
        "When retry_count >= max_retries the task is permanently failed — "
        "do not retry further unless the user explicitly asks. "
        "The user sees live Kanban updates: Todo → In Progress → Done / Failed."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "create_list",
                    "start_task",
                    "complete_task",
                    "fail_task",
                    "retry_task",
                    "add_task",
                    "delete_task",
                    "update_title",
                ],
                "description": "Action to perform on the task list.",
            },
            "tasks": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Task titles. Required for create_list and add_task.",
            },
            "task_id": {
                "type": "string",
                "description": (
                    "ID of the task to update. "
                    "If omitted for start_task / complete_task / retry_task, "
                    "the first matching task is used automatically."
                ),
            },
            "title": {
                "type": "string",
                "description": "New title for update_title action.",
            },
            "max_retries": {
                "type": "integer",
                "description": (
                    "Max retry attempts per failed task (default 3). "
                    "Only used with create_list."
                ),
                "default": 3,
            },
            "thread_id": {
                "type": "string",
                "description": "Conversation / thread ID (injected by the framework).",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(self, store: Any) -> None:
        self._store = store
        # task_list_id per conversation thread (supports concurrent requests)
        self._task_lists: Dict[str, str | None] = {}  # thread_id -> task_list_id

    def reset(self) -> None:
        """Reset between conversations (clears the given thread_id)."""
        tid = current_thread_id.get()
        self._task_lists.pop(tid, None)

    # ------------------------------------------------------------------
    # Execute (called by the ReAct agent)
    # ------------------------------------------------------------------

    async def execute(  # type: ignore[override]
        self,
        *,
        action: str,
        tasks: list[str] | None = None,
        task_id: str | None = None,
        title: str | None = None,
        max_retries: int = 3,
        thread_id: str | None = None,
    ) -> ToolExecutionResult:

        store = self._store
        # Prefer thread_id from ContextVar (set by chat route per-request),
        # fall back to tool argument, then to "default".
        conv_id = current_thread_id.get() or thread_id or "default"
        task_list_id = self._task_lists.get(conv_id)

        # ── create_list ──────────────────────────────────────────────
        if action == "create_list":
            if not tasks:
                return _err("tasks[] is required for create_list")

            task_list = await store.create_task_list(
                conv_id, tasks, max_retries=max_retries
            )
            self._task_lists[conv_id] = task_list.id

            names = "\n".join(f"  {i + 1}. {t}" for i, t in enumerate(tasks))
            return self._board_result(
                f"Task list created ({len(tasks)} tasks):\n{names}\n\n"
                f"Now call start_task (no task_id needed — auto-advances) "
                f"before each step, and complete_task after.",
                task_list.to_dict(),
            )

        # ── shared guard: need an active task list ────────────────────
        if not task_list_id:
            # Attempt to auto-recover from conversation store
            existing = await store.get_by_conversation(conv_id)
            if existing:
                task_list_id = existing.id
                self._task_lists[conv_id] = task_list_id
            else:
                return _err("No active task list. Call action=create_list first.")

        # ── start_task ───────────────────────────────────────────────
        if action == "start_task":
            resolved = await self._resolve_task_id(task_id, "todo", store, task_list_id)
            if not resolved:
                return _err("No todo tasks left to start.")

            updated = await store.update_status(task_list_id, resolved, "in_progress")
            if not updated:
                return _err(f"Task not found after resolution (id={resolved!r}).")

            return self._board_result(
                f"Started: {updated.title}", await self._board(store, task_list_id)
            )

        # ── complete_task ─────────────────────────────────────────────
        if action == "complete_task":
            resolved = await self._resolve_task_id(
                task_id, "in_progress", store, task_list_id
            )
            if not resolved:
                return _err("No in-progress tasks to complete.")

            updated = await store.update_status(task_list_id, resolved, "done")
            if not updated:
                return _err(f"Task not found after resolution (id={resolved!r}).")

            return self._board_result(
                f"Completed: {updated.title}", await self._board(store, task_list_id)
            )
        # ── fail_task ─────────────────────────────────────────────
        if action == "fail_task":
            resolved = await self._resolve_task_id(
                task_id, "in_progress", store, task_list_id
            )
            if not resolved:
                # Fall back to any todo task if none is in progress
                resolved = await self._resolve_task_id(task_id, "todo", store, task_list_id)
            if not resolved:
                return _err("No in-progress or todo task to mark as failed.")

            updated = await store.update_status(task_list_id, resolved, "failed")
            if not updated:
                return _err(f"Task not found after resolution (id={resolved!r}).")

            return self._board_result(
                f"Marked as failed: {updated.title}", await self._board(store, task_list_id)
            )

        # ── retry_task ────────────────────────────────────────────────
        if action == "retry_task":
            resolved = await self._resolve_task_id(task_id, "failed", store, task_list_id)
            if not resolved:
                return _err("No failed task found to retry.")

            task_list = await store.get_task_list(task_list_id)
            if not task_list:
                return _err("Task list not found.")

            task = next((t for t in task_list.tasks if t.id == resolved), None)
            if not task:
                return _err("Task not found.")

            if task.retry_count >= task_list.max_retries:
                return _err(
                    f"Task '{task.title}' has reached the retry limit "
                    f"({task_list.max_retries}). Mark it as permanently failed "
                    "or ask the user before retrying."
                )

            updated = await store.increment_retry(task_list_id, resolved)
            if not updated:
                return _err(f"Failed to retry task (id={resolved!r}).")

            return self._board_result(
                f"Retrying '{updated.title}' "
                f"(attempt {updated.retry_count}/{task_list.max_retries}).",
                await self._board(store, task_list_id),
            )

        # ── add_task ─────────────────────────────────────────────────
        if action == "add_task":
            if not tasks:
                return _err("tasks[] is required for add_task")

            new_tasks = await store.add_tasks(task_list_id, tasks)
            return self._board_result(
                f"Added {len(new_tasks)} task(s).", await self._board(store, task_list_id)
            )

        # ── delete_task ───────────────────────────────────────────────
        if action == "delete_task":
            if not task_id:
                return _err("task_id is required for delete_task")

            deleted = await store.delete_task(task_list_id, task_id)
            if not deleted:
                return _err(f"Task {task_id!r} not found.")

            return self._board_result(
                f"Deleted task {task_id}.", await self._board(store, task_list_id)
            )

        # ── update_title ──────────────────────────────────────────────
        if action == "update_title":
            if not task_id or not title:
                return _err("task_id and title are required for update_title")

            updated = await store.update_task_title(task_list_id, task_id, title)
            if not updated:
                return _err(f"Task {task_id!r} not found.")

            return self._board_result(
                f"Renamed task to: {updated.title}", await self._board(store, task_list_id)
            )

        return _err(f"Unknown action: {action!r}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _first_with_status(
        self, status: str, store: Any, task_list_id: str | None
    ) -> str | None:
        """Return the first task ID matching the given status."""
        if not task_list_id:
            return None
        task_list = await store.get_task_list(task_list_id)
        if not task_list:
            return None
        for task in task_list.tasks:
            if task.status == status:
                return task.id
        return None

    async def _resolve_task_id(
        self,
        task_id: str | None,
        status: str,
        store: Any,
        task_list_id: str,
    ) -> str | None:
        """Resolve a task_id flexibly so the agent rarely fails.

        Resolution order:
          1. No task_id supplied          → auto-advance (first task with matching status)
          2. Exact UUID match             → use it
          3. 1-based integer ('1','2'…)  → nth task in the matching-status list
          4. Case-insensitive title match → use that task's real id
          5. Fallback                    → auto-advance regardless of supplied value
        """
        task_list = await store.get_task_list(task_list_id)
        if not task_list:
            return None

        # 1. No hint → auto-advance
        if not task_id:
            return await self._first_with_status(status, store, task_list_id)

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
        return await self._first_with_status(status, store, task_list_id)

    @staticmethod
    async def _board(store: Any, task_list_id: str) -> Dict[str, Any]:
        """Return the full current board as a dict (empty when missing)."""
        tl = await store.get_task_list(task_list_id)
        return tl.to_dict() if tl else {}

    @staticmethod
    def _board_result(message: str, task_list: Dict[str, Any]) -> ToolExecutionResult:
        """A success result that also carries the board for the kanban UI.

        ``content`` is the model-facing text; ``structured_content`` is the
        UI-facing board the agent lowers into a ``ui://kanban_board``
        UIResourceBlock.  The board is sent in full each call so the iframe
        always renders the complete, current state.
        """
        return ToolExecutionResult(
            content=[TextBlock(text=message)],
            is_error=False,
            structured_content={"task_list": task_list},
        )


# ---------------------------------------------------------------------------
# Tiny helpers
# ---------------------------------------------------------------------------


def _err(message: str) -> ToolExecutionResult:
    return ToolExecutionResult(
        content=[TextBlock(text=message)],
        is_error=True,
    )
