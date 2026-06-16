"""PgTaskStore — Postgres-backed Kanban task store.

Schema::

    CREATE TABLE ravi_task_lists (
        id              TEXT        NOT NULL PRIMARY KEY,
        conversation_id TEXT        NOT NULL UNIQUE,
        max_retries     INTEGER     NOT NULL DEFAULT 3,
        tasks           JSONB       NOT NULL DEFAULT '[]',
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    );

Each row holds one task list (one per conversation).  Mutations read the row,
update in Python, and write the full JSONB back — adequate for a single-user
Kanban board with typical <50 tasks.

Compatible with ``TaskStore`` (``agents/storage/tasks.py``): both expose the
same ``async`` interface so ``TaskManagerTool`` and routes are store-agnostic.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, List, Optional
from uuid import uuid4

from ravi.agents.storage.tasks import Task, TaskList

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS ravi_task_lists (
    id              TEXT        NOT NULL PRIMARY KEY,
    conversation_id TEXT        NOT NULL UNIQUE,
    max_retries     INTEGER     NOT NULL DEFAULT 3,
    tasks           JSONB       NOT NULL DEFAULT '[]',
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_SELECT_BY_ID = "SELECT id, conversation_id, max_retries, tasks FROM ravi_task_lists WHERE id = :id"
_SELECT_BY_CONV = "SELECT id, conversation_id, max_retries, tasks FROM ravi_task_lists WHERE conversation_id = :cid"
_UPDATE_TASKS = "UPDATE ravi_task_lists SET tasks = CAST(:tasks AS jsonb), updated_at = now() WHERE id = :id"


class PgTaskStore:
    """Postgres-backed task store using the SQLAlchemy async session factory."""

    def __init__(self, session_factory: "async_sessionmaker[AsyncSession]") -> None:
        self._factory = session_factory

    async def setup(self) -> None:
        from sqlalchemy import text

        async with self._factory() as session:
            await session.execute(text(_CREATE_TABLE))
            await session.commit()

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def create_task_list(
        self,
        conversation_id: str,
        task_titles: List[str],
        max_retries: int = 3,
    ) -> TaskList:
        from sqlalchemy import text

        task_list = TaskList(
            id=str(uuid4()),
            conversation_id=conversation_id,
            max_retries=max_retries,
            tasks=[
                Task(id=str(uuid4()), title=t.strip(), status="todo", order=i)
                for i, t in enumerate(task_titles)
                if t.strip()
            ],
        )
        tasks_json = json.dumps([_task_to_dict(t) for t in task_list.tasks])
        async with self._factory() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO ravi_task_lists (id, conversation_id, max_retries, tasks)
                    VALUES (:id, :conversation_id, :max_retries, CAST(:tasks AS jsonb))
                    ON CONFLICT (conversation_id) DO UPDATE
                        SET id = EXCLUDED.id,
                            max_retries = EXCLUDED.max_retries,
                            tasks = EXCLUDED.tasks,
                            updated_at = now()
                    """
                ),
                {
                    "id": task_list.id,
                    "conversation_id": conversation_id,
                    "max_retries": max_retries,
                    "tasks": tasks_json,
                },
            )
            await session.commit()
        return task_list

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_task_list(self, task_list_id: str) -> Optional[TaskList]:
        from sqlalchemy import text

        async with self._factory() as session:
            result = await session.execute(text(_SELECT_BY_ID), {"id": task_list_id})
            row = result.first()
        return _row_to_task_list(row) if row else None

    async def get_by_conversation(self, conversation_id: str) -> Optional[TaskList]:
        from sqlalchemy import text

        async with self._factory() as session:
            result = await session.execute(text(_SELECT_BY_CONV), {"cid": conversation_id})
            row = result.first()
        return _row_to_task_list(row) if row else None

    # ------------------------------------------------------------------
    # Update status
    # ------------------------------------------------------------------

    async def update_status(
        self, task_list_id: str, task_id: str, status: str
    ) -> Optional[Task]:
        task_list = await self.get_task_list(task_list_id)
        if not task_list:
            return None
        updated: Task | None = None
        for task in task_list.tasks:
            if task.id == task_id:
                task.status = status
                updated = task
                break
        if updated is None:
            return None
        await self._save_tasks(task_list)
        return updated

    # ------------------------------------------------------------------
    # Add / Delete
    # ------------------------------------------------------------------

    async def add_tasks(self, task_list_id: str, titles: List[str]) -> List[Task]:
        task_list = await self.get_task_list(task_list_id)
        if not task_list:
            return []
        start = len(task_list.tasks)
        new_tasks = [
            Task(id=str(uuid4()), title=t.strip(), status="todo", order=start + i)
            for i, t in enumerate(titles)
            if t.strip()
        ]
        task_list.tasks.extend(new_tasks)
        await self._save_tasks(task_list)
        return new_tasks

    async def delete_task(self, task_list_id: str, task_id: str) -> bool:
        task_list = await self.get_task_list(task_list_id)
        if not task_list:
            return False
        before = len(task_list.tasks)
        task_list.tasks = [t for t in task_list.tasks if t.id != task_id]
        if len(task_list.tasks) == before:
            return False
        await self._save_tasks(task_list)
        return True

    async def increment_retry(self, task_list_id: str, task_id: str) -> Optional[Task]:
        task_list = await self.get_task_list(task_list_id)
        if not task_list:
            return None
        updated: Task | None = None
        for task in task_list.tasks:
            if task.id == task_id:
                task.retry_count += 1
                task.status = "in_progress"
                updated = task
                break
        if updated is None:
            return None
        await self._save_tasks(task_list)
        return updated

    async def update_task_title(
        self, task_list_id: str, task_id: str, title: str
    ) -> Optional[Task]:
        task_list = await self.get_task_list(task_list_id)
        if not task_list:
            return None
        updated: Task | None = None
        for task in task_list.tasks:
            if task.id == task_id:
                task.title = title.strip()
                updated = task
                break
        if updated is None:
            return None
        await self._save_tasks(task_list)
        return updated

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _save_tasks(self, task_list: TaskList) -> None:
        from sqlalchemy import text

        tasks_json = json.dumps([_task_to_dict(t) for t in task_list.tasks])
        async with self._factory() as session:
            await session.execute(
                text(_UPDATE_TASKS),
                {"tasks": tasks_json, "id": task_list.id},
            )
            await session.commit()


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------


def _task_to_dict(t: Task) -> dict:
    return {
        "id": t.id,
        "title": t.title,
        "status": t.status,
        "order": t.order,
        "retry_count": t.retry_count,
    }


def _row_to_task_list(row: object) -> TaskList:
    m = row._mapping  # type: ignore[union-attr]
    tasks_raw = m["tasks"]
    tasks_data: list = json.loads(tasks_raw) if isinstance(tasks_raw, str) else (tasks_raw or [])
    return TaskList(
        id=m["id"],
        conversation_id=m["conversation_id"],
        max_retries=m["max_retries"],
        tasks=[
            Task(
                id=t["id"],
                title=t["title"],
                status=t.get("status", "todo"),
                order=t.get("order", 0),
                retry_count=t.get("retry_count", 0),
            )
            for t in tasks_data
        ],
    )


__all__ = ["PgTaskStore"]
