"""Task storage contract — per-agent Kanban board with a 6-state lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class TaskStatus(StrEnum):
    """6-state lifecycle for a task.

    Transitions::

        planned ──start──▶ in_progress ──complete──▶ succeeded (terminal)
                             │   ▲  │
                    block ──▶│   │  └──fail──▶ failed ──retry (count<max)──▶ in_progress
                             ▼   │                │
                          blocked│                └──retries exhausted──▶ abandoned (terminal)
                          (unblock → in_progress)
    """

    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABANDONED = "abandoned"


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    status: TaskStatus = TaskStatus.PLANNED
    order: int = 0
    retry_count: int = 0
    note: str = ""


@dataclass(frozen=True)
class TaskList:
    """One agent's Kanban board within a conversation."""

    id: str
    conversation_id: str
    tasks: list[Task] = field(default_factory=list)
    max_retries: int = 3
    agent_id: str = ""
    agent_label: str = ""
    parent_agent_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "max_retries": self.max_retries,
            "agent_id": self.agent_id,
            "agent_label": self.agent_label,
            "parent_agent_id": self.parent_agent_id,
            "tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "status": t.status,
                    "order": t.order,
                    "retry_count": t.retry_count,
                    "max_retries": self.max_retries,
                    "note": t.note,
                }
                for t in self.tasks
            ],
        }


class TaskStore(Protocol):
    """Durable storage for per-agent Kanban boards, scoped by conversation."""

    async def create_task_list(
        self,
        conversation_id: str,
        task_titles: list[str],
        *,
        agent_id: str = "",
        agent_label: str = "",
        parent_agent_id: str | None = None,
        max_retries: int = 3,
    ) -> TaskList:
        """Create (or replace) the board for (conversation_id, agent_id)."""
        ...

    async def get_task_list(self, task_list_id: str) -> TaskList | None:
        """Fetch a board by its own id."""
        ...

    async def get_by_conversation(self, conversation_id: str) -> TaskList | None:
        """Return the first/primary board for a conversation (backwards compat)."""
        ...

    async def get_boards_by_conversation(self, conversation_id: str) -> list[TaskList]:
        """Return all agent boards for a conversation (including subagents)."""
        ...

    async def update_status(
        self, task_list_id: str, task_id: str, status: TaskStatus | str, note: str = ""
    ) -> Task | None:
        """Update a task's status (and optional note)."""
        ...

    async def add_tasks(self, task_list_id: str, titles: list[str]) -> list[Task]:
        """Append new tasks to a board."""
        ...

    async def delete_task(self, task_list_id: str, task_id: str) -> bool:
        """Remove a task; returns True if found and deleted."""
        ...

    async def increment_retry(self, task_list_id: str, task_id: str) -> Task | None:
        """Agent retry: increment retry_count, move to in_progress.

        Returns None when the task is not found OR retry_count has reached
        max_retries (caller must set status to abandoned in that case).
        """
        ...

    async def force_retry(self, task_list_id: str, task_id: str) -> Task | None:
        """User override: reset retry_count to 0 and set in_progress.

        Works on both failed and abandoned tasks.
        """
        ...

    async def update_task_title(
        self, task_list_id: str, task_id: str, title: str
    ) -> Task | None:
        """Rename a task."""
        ...
