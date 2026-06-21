"""
Task Manager Tool — lets the agent create a visible Kanban task list.

6-state lifecycle: planned → in_progress → succeeded (terminal)
                             ↕ block/unblock
                           blocked
                   in_progress → failed → retry → in_progress (until max_retries)
                                failed → abandoned (terminal, when retries exhausted)

Usage pattern:
  1. create_list  — before starting work
  2. start_task   — planned/blocked → in_progress
  3. complete_task — in_progress → succeeded
  4. fail_task    — in_progress/blocked → failed (+ optional note)
  5. retry_task   — failed → in_progress (auto-advances to abandoned when exhausted)
  6. block_task   — in_progress → blocked (+ optional note)
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from agent_substrate.kernel.storage.tasks import TaskStatus
from agent_substrate.kernel.tools import ToolExecutionResult, ToolUI
from agent_substrate.kernel import TextBlock
from agent_substrate.logger import setup_logging
from agent_substrate.agents.storage.tasks import (
    current_thread_id,
    current_agent_id,
    current_agent_label,
    current_parent_agent_id,
)

logger = setup_logging()


class TaskManagerTool:
    """
    Manage a visible Kanban task list during complex agent runs.

    Actions
    -------
    create_list   – Create a task board (call first, before any work).
    start_task    – planned/blocked → in_progress.
    complete_task – in_progress → succeeded.
    fail_task     – in_progress/blocked → failed (optional note).
    block_task    – in_progress → blocked (optional note — reason for blocking).
    retry_task    – failed → in_progress (auto-sets abandoned when retries exhausted).
    add_task      – Append new tasks to the current board.
    delete_task   – Remove a task by ID.
    update_title  – Rename a task.
    """

    risk: str = "safe"

    ui: ToolUI = ToolUI(resource_uri="ui://kanban_board", prefers_border=True)

    name: str = "manage_tasks"
    description: str = (
        "Create and update a visible task-board for complex, multi-step work. "
        "ALWAYS call action=create_list FIRST with all planned steps. "
        "Work strictly ONE step at a time: call start_task, do the actual work, "
        "then call complete_task BEFORE you start the next step. "
        "Never leave a step in progress — you MUST complete (or fail) the final "
        "step before writing your final answer to the user. "
        "Call fail_task (with an optional note explaining why) when a step cannot complete. "
        "Call block_task when a step is waiting on something external. "
        "Call retry_task on failed tasks — it auto-advances to 'abandoned' when retries are exhausted. "
        "The user sees a live board: Planned → In Progress → Succeeded / Failed / Blocked."
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
                    "block_task",
                    "retry_task",
                    "add_task",
                    "delete_task",
                    "update_title",
                ],
                "description": "Action to perform on the task board.",
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
            "note": {
                "type": "string",
                "description": "Short reason shown in the UI (used by fail_task and block_task).",
            },
            "max_retries": {
                "type": "integer",
                "description": "Max retry attempts per failed task (default 3). Only used with create_list.",
                "default": 3,
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        store: Any,
        *,
        event_sink: Callable[[str, dict], Awaitable[None]] | None = None,
    ) -> None:
        self._store = store
        # (conv_id, agent_id) -> task_list_id
        self._task_lists: Dict[tuple[str, str], str | None] = {}
        # Optional (conversation_id, board_dict) sink used to stream subagent
        # board updates live — their run's events don't reach the parent stream.
        self._event_sink = event_sink

    @property
    def store(self) -> Any:
        return self._store

    def reset(self) -> None:
        """Reset the board pointer for the current thread/agent."""
        tid = current_thread_id.get()
        aid = current_agent_id.get()
        self._task_lists.pop((tid, aid), None)

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    async def execute(  # type: ignore[override]
        self,
        *,
        ctx: Any = None,
        action: str,
        tasks: list[str] | None = None,
        task_id: str | None = None,
        title: str | None = None,
        note: str | None = None,
        max_retries: int = 3,
        thread_id: str | None = None,
    ) -> ToolExecutionResult:

        store = self._store
        conv_id = current_thread_id.get() or thread_id or "default"
        agent_id = current_agent_id.get()
        agent_label = current_agent_label.get()
        parent_agent_id = current_parent_agent_id.get()

        cache_key = (conv_id, agent_id)
        task_list_id = self._task_lists.get(cache_key)

        # ── create_list ──────────────────────────────────────────────
        if action == "create_list":
            if not tasks:
                return _err("tasks[] is required for create_list")

            tasks = _dedupe_titles(tasks)

            task_list = await store.create_task_list(
                conv_id,
                tasks,
                agent_id=agent_id,
                agent_label=agent_label,
                parent_agent_id=parent_agent_id,
                max_retries=max_retries,
            )
            self._task_lists[cache_key] = task_list.id

            names = "\n".join(f"  {i + 1}. {t}" for i, t in enumerate(tasks))
            return await self._board_result(
                f"Task board created ({len(tasks)} tasks):\n{names}\n\n"
                f"Now call start_task before each step and complete_task after.",
                task_list.to_dict(),
            )

        # ── shared guard: need an active task list ────────────────────
        if not task_list_id:
            existing = await store.get_by_conversation(conv_id)
            if existing:
                task_list_id = existing.id
                self._task_lists[cache_key] = task_list_id
            else:
                return _err("No active task board. Call action=create_list first.")

        # ── start_task ───────────────────────────────────────────────
        if action == "start_task":
            # Enforce one-at-a-time: auto-complete any task left in_progress so
            # the board always advances even if the model forgot complete_task.
            stale = await self._first_with_status(
                TaskStatus.IN_PROGRESS, store, task_list_id
            )
            while stale:
                await store.update_status(task_list_id, stale, TaskStatus.SUCCEEDED)
                stale = await self._first_with_status(
                    TaskStatus.IN_PROGRESS, store, task_list_id
                )

            # Accept planned or blocked tasks
            resolved = await self._resolve_task_id(
                task_id, TaskStatus.PLANNED, store, task_list_id
            )
            if not resolved:
                resolved = await self._resolve_task_id(
                    task_id, TaskStatus.BLOCKED, store, task_list_id
                )
            if not resolved:
                return _err("No planned or blocked tasks to start.")

            updated = await store.update_status(
                task_list_id, resolved, TaskStatus.IN_PROGRESS
            )
            if not updated:
                return _err(f"Task not found after resolution (id={resolved!r}).")

            return await self._board_result(
                f"Started: {updated.title}", await self._board(store, task_list_id)
            )

        # ── complete_task ─────────────────────────────────────────────
        if action == "complete_task":
            resolved = await self._resolve_task_id(
                task_id, TaskStatus.IN_PROGRESS, store, task_list_id
            )
            if not resolved:
                return _err("No in-progress tasks to complete.")

            updated = await store.update_status(
                task_list_id, resolved, TaskStatus.SUCCEEDED
            )
            if not updated:
                return _err(f"Task not found after resolution (id={resolved!r}).")

            return await self._board_result(
                f"Completed: {updated.title}", await self._board(store, task_list_id)
            )

        # ── fail_task ─────────────────────────────────────────────────
        if action == "fail_task":
            resolved = await self._resolve_task_id(
                task_id, TaskStatus.IN_PROGRESS, store, task_list_id
            )
            if not resolved:
                resolved = await self._resolve_task_id(
                    task_id, TaskStatus.BLOCKED, store, task_list_id
                )
            if not resolved:
                return _err("No in-progress or blocked task to mark as failed.")

            updated = await store.update_status(
                task_list_id, resolved, TaskStatus.FAILED, note=note or ""
            )
            if not updated:
                return _err(f"Task not found after resolution (id={resolved!r}).")

            return await self._board_result(
                f"Marked as failed: {updated.title}",
                await self._board(store, task_list_id),
            )

        # ── block_task ────────────────────────────────────────────────
        if action == "block_task":
            resolved = await self._resolve_task_id(
                task_id, TaskStatus.IN_PROGRESS, store, task_list_id
            )
            if not resolved:
                return _err("No in-progress task to block.")

            updated = await store.update_status(
                task_list_id, resolved, TaskStatus.BLOCKED, note=note or ""
            )
            if not updated:
                return _err(f"Task not found after resolution (id={resolved!r}).")

            return await self._board_result(
                f"Blocked: {updated.title}", await self._board(store, task_list_id)
            )

        # ── retry_task ────────────────────────────────────────────────
        if action == "retry_task":
            resolved = await self._resolve_task_id(
                task_id, TaskStatus.FAILED, store, task_list_id
            )
            if not resolved:
                return _err("No failed task found to retry.")

            updated = await store.increment_retry(task_list_id, resolved)
            if not updated:
                # Retries exhausted — mark abandoned
                abandoned = await store.update_status(
                    task_list_id, resolved, TaskStatus.ABANDONED
                )
                title = abandoned.title if abandoned else resolved
                return await self._board_result(
                    f"Task '{title}' has exhausted all retries and is now abandoned.",
                    await self._board(store, task_list_id),
                )

            task_list = await store.get_task_list(task_list_id)
            max_r = task_list.max_retries if task_list else max_retries
            return await self._board_result(
                f"Retrying '{updated.title}' (attempt {updated.retry_count}/{max_r}).",
                await self._board(store, task_list_id),
            )

        # ── add_task ─────────────────────────────────────────────────
        if action == "add_task":
            if not tasks:
                return _err("tasks[] is required for add_task")

            # Skip titles already on the board so repeated or confused calls
            # can't pile up phantom duplicate steps.
            board = await store.get_task_list(task_list_id)
            existing = (
                {t.title.strip().lower() for t in board.tasks} if board else set()
            )
            fresh = [
                t for t in _dedupe_titles(tasks) if t.strip().lower() not in existing
            ]
            if not fresh:
                return await self._board_result(
                    "No new tasks added — all titles already exist on the board.",
                    await self._board(store, task_list_id),
                )

            new_tasks = await store.add_tasks(task_list_id, fresh)
            return await self._board_result(
                f"Added {len(new_tasks)} task(s).",
                await self._board(store, task_list_id),
            )

        # ── delete_task ───────────────────────────────────────────────
        if action == "delete_task":
            if not task_id:
                return _err("task_id is required for delete_task")

            deleted = await store.delete_task(task_list_id, task_id)
            if not deleted:
                return _err(f"Task {task_id!r} not found.")

            return await self._board_result(
                f"Deleted task {task_id}.", await self._board(store, task_list_id)
            )

        # ── update_title ──────────────────────────────────────────────
        if action == "update_title":
            if not task_id or not title:
                return _err("task_id and title are required for update_title")

            updated = await store.update_task_title(task_list_id, task_id, title)
            if not updated:
                return _err(f"Task {task_id!r} not found.")

            return await self._board_result(
                f"Renamed task to: {updated.title}",
                await self._board(store, task_list_id),
            )

        return _err(f"Unknown action: {action!r}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _first_with_status(
        self, status: str, store: Any, task_list_id: str | None
    ) -> str | None:
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
        task_list = await store.get_task_list(task_list_id)
        if not task_list:
            return None

        if not task_id:
            return await self._first_with_status(status, store, task_list_id)

        for task in task_list.tasks:
            if task.id == task_id:
                return task.id

        try:
            idx = int(task_id) - 1
            subset = [t for t in task_list.tasks if t.status == status]
            if 0 <= idx < len(subset):
                return subset[idx].id
            if 0 <= idx < len(task_list.tasks):
                return task_list.tasks[idx].id
        except (ValueError, TypeError):
            pass

        needle = task_id.lower()
        for task in task_list.tasks:
            if needle in task.title.lower():
                return task.id

        return await self._first_with_status(status, store, task_list_id)

    @staticmethod
    async def _board(store: Any, task_list_id: str) -> Dict[str, Any]:
        tl = await store.get_task_list(task_list_id)
        return tl.to_dict() if tl else {}

    async def _board_result(
        self, message: str, task_list: Dict[str, Any]
    ) -> ToolExecutionResult:
        # Stream subagent board updates onto the thread bridge so they appear
        # live; root-agent boards already flow via the run's event-log tail.
        if self._event_sink and task_list.get("parent_agent_id"):
            conv_id = task_list.get("conversation_id")
            if conv_id:
                try:
                    await self._event_sink(str(conv_id), task_list)
                except Exception:
                    logger.debug("board event sink failed", exc_info=True)
        return ToolExecutionResult(
            content=[TextBlock(text=message)],
            is_error=False,
            structured_content={"task_list": task_list},
        )


def _dedupe_titles(titles: list[str]) -> list[str]:
    """Drop blank and case-insensitively duplicate titles, preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for title in titles:
        key = title.strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(title)
    return result


def _err(message: str) -> ToolExecutionResult:
    return ToolExecutionResult(
        content=[TextBlock(text=message)],
        is_error=True,
    )
