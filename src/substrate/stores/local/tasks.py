"""LocalFilesystemTaskStore — per-agent Kanban boards as JSON files.

Layout::

    <root>/boards/<board_id>.json     — one file per TaskList

A board is keyed by ``(conversation_id, agent_id, branch_id)``; creating one for an existing key
replaces the old board (its file is removed). Lookups scan the boards directory, which is correct at
local scale. Writes go through one lock and are atomic, so a crash never leaves half a board.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from substrate.kernel.abstractions.storage.tasks import Task, TaskList, TaskStatus
from substrate.kernel.storage.fs import atomic_write_json, safe_name


class LocalFilesystemTaskStore:
    def __init__(self, root: str | Path = "./data/db/tasks") -> None:
        self._dir = Path(root) / "boards"
        self._lock = asyncio.Lock()

    # ── helpers ──────────────────────────────────────────────────────────

    def _path(self, board_id: str) -> Path:
        return self._dir / f"{safe_name(board_id)}.json"

    def _load(self, path: Path) -> TaskList | None:
        try:
            return TaskList.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return None

    def _save(self, board: TaskList) -> None:
        atomic_write_json(self._path(board.id), json.loads(board.model_dump_json()))

    def _all(self) -> list[TaskList]:
        if not self._dir.exists():
            return []
        boards = [b for p in self._dir.glob("*.json") if (b := self._load(p)) is not None]
        return sorted(boards, key=lambda b: b.created_at)

    async def _edit_task(self, board_id: str, task_id: str, change: Callable[[TaskList, Task], Task | None]) -> Task | None:
        """Apply ``change`` to one task and persist; ``None`` from ``change`` means "refuse"."""
        async with self._lock:
            board = self.get_sync(board_id)
            if board is None:
                return None
            for task in board.tasks:
                if task.id == task_id:
                    updated = change(board, task)
                    if updated is None:
                        return None
                    self._save(board.model_copy(update={"tasks": [updated if t.id == task_id else t for t in board.tasks]}))
                    return updated
            return None

    def get_sync(self, board_id: str) -> TaskList | None:
        path = self._path(board_id)
        return self._load(path) if path.exists() else None

    # ── TaskStore ────────────────────────────────────────────────────────

    async def create_task_list(
        self,
        conversation_id: str,
        task_titles: list[str],
        *,
        agent_id: str = "",
        agent_label: str = "",
        parent_agent_id: str | None = None,
        max_retries: int = 3,
        branch_id: str = "main",
    ) -> TaskList:
        async with self._lock:
            board = TaskList(
                id=str(uuid4()),
                conversation_id=conversation_id,
                max_retries=max_retries,
                agent_id=agent_id,
                agent_label=agent_label,
                parent_agent_id=parent_agent_id,
                branch_id=branch_id,
                created_at=datetime.now(timezone.utc).isoformat(),
                tasks=[
                    Task(id=str(uuid4()), title=t.strip(), status=TaskStatus.PLANNED, order=i)
                    for i, t in enumerate(t for t in task_titles if t.strip())
                ],
            )
            for old in self._all():
                if (old.conversation_id, old.agent_id, old.branch_id) == (conversation_id, agent_id, branch_id):
                    self._path(old.id).unlink(missing_ok=True)
            self._save(board)
            return board

    async def get_task_list(self, task_list_id: str) -> TaskList | None:
        return self.get_sync(task_list_id)

    async def get_boards_by_conversation(self, conversation_id: str, branch_id: str = "main") -> list[TaskList]:
        return [b for b in self._all() if b.conversation_id == conversation_id and b.branch_id == branch_id]

    async def get_by_conversation(self, conversation_id: str, branch_id: str = "main") -> TaskList | None:
        """The root board (``agent_id == ""``), else any board for this conversation on this branch."""
        boards = await self.get_boards_by_conversation(conversation_id, branch_id)
        return next((b for b in boards if b.agent_id == ""), boards[0] if boards else None)

    async def settle_conversation(self, conversation_id: str) -> list[TaskList]:
        """Flip any lingering ``in_progress`` task of the main branch to ``succeeded`` when a run ends,
        so the board stops spinning. Returns the boards that changed."""
        async with self._lock:
            changed: list[TaskList] = []
            for board in self._all():
                if board.conversation_id != conversation_id or board.branch_id != "main":
                    continue
                if any(t.status == TaskStatus.IN_PROGRESS for t in board.tasks):
                    updated = board.model_copy(
                        update={
                            "tasks": [
                                t.model_copy(update={"status": TaskStatus.SUCCEEDED}) if t.status == TaskStatus.IN_PROGRESS else t
                                for t in board.tasks
                            ]
                        }
                    )
                    self._save(updated)
                    changed.append(updated)
            return changed

    async def update_status(self, task_list_id: str, task_id: str, status: TaskStatus, note: str = "") -> Task | None:
        return await self._edit_task(task_list_id, task_id, lambda _b, t: t.model_copy(update={"status": status, "note": note or t.note}))

    async def add_tasks(self, task_list_id: str, titles: list[str]) -> list[Task]:
        async with self._lock:
            board = self.get_sync(task_list_id)
            if board is None:
                return []
            start = len(board.tasks)
            added = [
                Task(id=str(uuid4()), title=t.strip(), status=TaskStatus.PLANNED, order=start + i)
                for i, t in enumerate(t for t in titles if t.strip())
            ]
            self._save(board.model_copy(update={"tasks": [*board.tasks, *added]}))
            return added

    async def delete_task(self, task_list_id: str, task_id: str) -> bool:
        async with self._lock:
            board = self.get_sync(task_list_id)
            if board is None:
                return False
            kept = [t for t in board.tasks if t.id != task_id]
            if len(kept) == len(board.tasks):
                return False
            self._save(board.model_copy(update={"tasks": kept}))
            return True

    async def increment_retry(self, task_list_id: str, task_id: str) -> Task | None:
        """Agent bounded retry: count it and move to in_progress; ``None`` once the maximum is reached."""
        return await self._edit_task(
            task_list_id,
            task_id,
            lambda b, t: None if t.retry_count >= b.max_retries else t.model_copy(update={"retry_count": t.retry_count + 1, "status": TaskStatus.IN_PROGRESS}),
        )

    async def force_retry(self, task_list_id: str, task_id: str) -> Task | None:
        """User override: reset the count and set in_progress."""
        return await self._edit_task(
            task_list_id, task_id, lambda _b, t: t.model_copy(update={"retry_count": 0, "status": TaskStatus.IN_PROGRESS, "note": ""})
        )

    async def update_task_title(self, task_list_id: str, task_id: str, title: str) -> Task | None:
        return await self._edit_task(task_list_id, task_id, lambda _b, t: t.model_copy(update={"title": title.strip()}))


__all__ = ["LocalFilesystemTaskStore"]
