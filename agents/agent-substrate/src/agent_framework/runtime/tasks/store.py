"""
Task Store — in-memory store for agent task lists.
Each conversation gets one active TaskList.
Design is intentionally simple; swap for Postgres/SQLAlchemy later.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from uuid import uuid4


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Task:
    id: str
    title: str
    status: str = "todo"   # "todo" | "in_progress" | "done"
    order: int = 0


@dataclass
class TaskList:
    id: str
    conversation_id: str
    tasks: List[Task] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "tasks": [
                {"id": t.id, "title": t.title, "status": t.status, "order": t.order}
                for t in self.tasks
            ],
        }


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class TaskStore:
    """Thread-safe in-memory task store (singleton via GlobalTaskStore)."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        # task_list_id -> TaskList
        self._lists: Dict[str, TaskList] = {}
        # conversation_id -> task_list_id  (one active list per conversation)
        self._by_conversation: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def create_task_list(
        self, conversation_id: str, task_titles: List[str]
    ) -> TaskList:
        async with self._lock:
            task_list = TaskList(
                id=str(uuid4()),
                conversation_id=conversation_id,
                tasks=[
                    Task(id=str(uuid4()), title=t.strip(), status="todo", order=i)
                    for i, t in enumerate(task_titles)
                    if t.strip()
                ],
            )
            self._lists[task_list.id] = task_list
            self._by_conversation[conversation_id] = task_list.id
            return task_list

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_task_list(self, task_list_id: str) -> Optional[TaskList]:
        return self._lists.get(task_list_id)

    def get_by_conversation(self, conversation_id: str) -> Optional[TaskList]:
        tl_id = self._by_conversation.get(conversation_id)
        if tl_id:
            return self._lists.get(tl_id)
        return None

    # ------------------------------------------------------------------
    # Update status
    # ------------------------------------------------------------------

    async def update_status(
        self, task_list_id: str, task_id: str, status: str
    ) -> Optional[Task]:
        async with self._lock:
            task_list = self._lists.get(task_list_id)
            if not task_list:
                return None
            for task in task_list.tasks:
                if task.id == task_id:
                    task.status = status
                    return task
            return None

    # ------------------------------------------------------------------
    # Add / Delete
    # ------------------------------------------------------------------

    async def add_tasks(self, task_list_id: str, titles: List[str]) -> List[Task]:
        async with self._lock:
            task_list = self._lists.get(task_list_id)
            if not task_list:
                return []
            start_order = len(task_list.tasks)
            new_tasks = [
                Task(id=str(uuid4()), title=t.strip(), status="todo", order=start_order + i)
                for i, t in enumerate(titles)
                if t.strip()
            ]
            task_list.tasks.extend(new_tasks)
            return new_tasks

    async def delete_task(self, task_list_id: str, task_id: str) -> bool:
        async with self._lock:
            task_list = self._lists.get(task_list_id)
            if not task_list:
                return False
            before = len(task_list.tasks)
            task_list.tasks = [t for t in task_list.tasks if t.id != task_id]
            return len(task_list.tasks) < before

    async def update_task_title(
        self, task_list_id: str, task_id: str, title: str
    ) -> Optional[Task]:
        async with self._lock:
            task_list = self._lists.get(task_list_id)
            if not task_list:
                return None
            for task in task_list.tasks:
                if task.id == task_id:
                    task.title = title.strip()
                    return task
            return None


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

class GlobalTaskStore:
    _instance: Optional[TaskStore] = None

    @classmethod
    def get(cls) -> TaskStore:
        if cls._instance is None:
            cls._instance = TaskStore()
        return cls._instance
