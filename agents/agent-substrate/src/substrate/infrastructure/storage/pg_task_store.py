"""PgTaskStore — Postgres-backed per-agent Kanban task store.

Schema (auto-created via setup())::

    CREATE TABLE substrate_task_lists (
        id              TEXT        NOT NULL PRIMARY KEY,
        conversation_id TEXT        NOT NULL,
        agent_id        TEXT        NOT NULL DEFAULT '',
        agent_label     TEXT        NOT NULL DEFAULT '',
        parent_agent_id TEXT        NULL,
        max_retries     INTEGER     NOT NULL DEFAULT 3,
        tasks           JSONB       NOT NULL DEFAULT '[]',
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (conversation_id, agent_id)
    );

NOTE: if you have an existing substrate_task_lists table it must be dropped
before starting — there is no migration framework; schema is declarative.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional
from uuid import uuid4

from substrate.kernel.storage.tasks import Task, TaskList, TaskStatus

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS substrate_task_lists (
    id              TEXT        NOT NULL PRIMARY KEY,
    conversation_id TEXT        NOT NULL,
    agent_id        TEXT        NOT NULL DEFAULT '',
    agent_label     TEXT        NOT NULL DEFAULT '',
    parent_agent_id TEXT        NULL,
    max_retries     INTEGER     NOT NULL DEFAULT 3,
    tasks           JSONB       NOT NULL DEFAULT '[]',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, agent_id)
)
"""

_SELECT_BY_ID = """
SELECT id, conversation_id, agent_id, agent_label, parent_agent_id, max_retries, tasks, created_at
FROM substrate_task_lists WHERE id = :id
"""

_SELECT_BY_CONV_AGENT = """
SELECT id, conversation_id, agent_id, agent_label, parent_agent_id, max_retries, tasks, created_at
FROM substrate_task_lists WHERE conversation_id = :cid AND agent_id = :aid
"""

_SELECT_ALL_BY_CONV = """
SELECT id, conversation_id, agent_id, agent_label, parent_agent_id, max_retries, tasks, created_at
FROM substrate_task_lists WHERE conversation_id = :cid
"""

_UPDATE_TASKS = """
UPDATE substrate_task_lists SET tasks = CAST(:tasks AS jsonb), updated_at = now() WHERE id = :id
"""


class PgTaskStore:
    """Postgres-backed task store using the SQLAlchemy async session factory."""

    def __init__(self, session_factory: "async_sessionmaker[AsyncSession]") -> None:
        self._factory = session_factory

    async def setup(self) -> None:
        from sqlalchemy import text

        async with self._factory() as session:
            await session.execute(text(_CREATE_TABLE))
            # Migrate existing tables that pre-date the per-agent schema.
            for col, defn in [
                ("agent_id", "TEXT NOT NULL DEFAULT ''"),
                ("agent_label", "TEXT NOT NULL DEFAULT ''"),
                ("parent_agent_id", "TEXT NULL"),
                ("created_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
            ]:
                await session.execute(
                    text(
                        f"ALTER TABLE substrate_task_lists ADD COLUMN IF NOT EXISTS {col} {defn}"
                    )
                )
            # Add unique constraint if missing (safe to run repeatedly via DO block).
            await session.execute(
                text(
                    "DO $$ BEGIN "
                    "IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'substrate_task_lists_conversation_id_agent_id_key') THEN "
                    "ALTER TABLE substrate_task_lists ADD CONSTRAINT substrate_task_lists_conversation_id_agent_id_key UNIQUE (conversation_id, agent_id); "
                    "END IF; END $$"
                )
            )
            await session.commit()

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def create_task_list(
        self,
        conversation_id: str,
        task_titles: List[str],
        *,
        agent_id: str = "",
        agent_label: str = "",
        parent_agent_id: Optional[str] = None,
        max_retries: int = 3,
    ) -> TaskList:
        from sqlalchemy import text

        task_list = TaskList(
            id=str(uuid4()),
            conversation_id=conversation_id,
            max_retries=max_retries,
            agent_id=agent_id,
            agent_label=agent_label,
            parent_agent_id=parent_agent_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            tasks=[
                Task(
                    id=str(uuid4()), title=t.strip(), status=TaskStatus.PLANNED, order=i
                )
                for i, t in enumerate(task_titles)
                if t.strip()
            ],
        )
        tasks_json = json.dumps([_task_to_dict(t) for t in task_list.tasks])
        async with self._factory() as session:
            # created_at is intentionally NOT updated on conflict so the board
            # stays anchored to the turn that first created it.
            result = await session.execute(
                text(
                    """
                    INSERT INTO substrate_task_lists
                        (id, conversation_id, agent_id, agent_label, parent_agent_id, max_retries, tasks)
                    VALUES (:id, :cid, :aid, :alabel, :paid, :max_retries, CAST(:tasks AS jsonb))
                    ON CONFLICT (conversation_id, agent_id) DO UPDATE
                        SET id = EXCLUDED.id,
                            agent_label = EXCLUDED.agent_label,
                            parent_agent_id = EXCLUDED.parent_agent_id,
                            max_retries = EXCLUDED.max_retries,
                            tasks = EXCLUDED.tasks,
                            updated_at = now()
                    RETURNING created_at
                    """
                ),
                {
                    "id": task_list.id,
                    "cid": conversation_id,
                    "aid": agent_id,
                    "alabel": agent_label,
                    "paid": parent_agent_id,
                    "max_retries": max_retries,
                    "tasks": tasks_json,
                },
            )
            row = result.first()
            await session.commit()
        if row is not None and row[0] is not None:
            created = row[0]
            task_list = dataclasses.replace(
                task_list,
                created_at=created.isoformat()
                if hasattr(created, "isoformat")
                else str(created),
            )
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
        """Return the root (agent_id='') board, or the first board found."""
        from sqlalchemy import text

        async with self._factory() as session:
            result = await session.execute(
                text(_SELECT_BY_CONV_AGENT), {"cid": conversation_id, "aid": ""}
            )
            row = result.first()
            if row:
                return _row_to_task_list(row)
            # fallback: any board
            result2 = await session.execute(
                text(_SELECT_ALL_BY_CONV), {"cid": conversation_id}
            )
            row2 = result2.first()
        return _row_to_task_list(row2) if row2 else None

    async def get_boards_by_conversation(self, conversation_id: str) -> List[TaskList]:
        from sqlalchemy import text

        async with self._factory() as session:
            result = await session.execute(
                text(_SELECT_ALL_BY_CONV), {"cid": conversation_id}
            )
            rows = result.fetchall()
        return [_row_to_task_list(r) for r in rows]

    async def settle_conversation(self, conversation_id: str) -> List[TaskList]:
        """Settle a conversation's boards when its run ends.

        Flips any lingering ``in_progress`` task to ``succeeded`` so the UI
        board stops spinning after the agent has produced its final answer.
        Planned/blocked/failed/abandoned tasks are left untouched. Returns the
        boards that changed.
        """
        boards = await self.get_boards_by_conversation(conversation_id)
        changed: List[TaskList] = []
        for task_list in boards:
            new_tasks = []
            mutated = False
            for task in task_list.tasks:
                if task.status == TaskStatus.IN_PROGRESS:
                    new_tasks.append(
                        dataclasses.replace(task, status=TaskStatus.SUCCEEDED)
                    )
                    mutated = True
                else:
                    new_tasks.append(task)
            if mutated:
                updated = dataclasses.replace(task_list, tasks=new_tasks)
                await self._save_tasks(updated)
                changed.append(updated)
        return changed

    # ------------------------------------------------------------------
    # Update status
    # ------------------------------------------------------------------

    async def update_status(
        self, task_list_id: str, task_id: str, status: str, note: str = ""
    ) -> Optional[Task]:
        task_list = await self.get_task_list(task_list_id)
        if not task_list:
            return None
        new_task: Task | None = None
        for task in task_list.tasks:
            if task.id == task_id:
                new_task = dataclasses.replace(
                    task, status=status, note=note if note else task.note
                )
                break
        if new_task is None:
            return None
        updated_list = dataclasses.replace(
            task_list,
            tasks=[new_task if t.id == task_id else t for t in task_list.tasks],
        )
        await self._save_tasks(updated_list)
        return new_task

    # ------------------------------------------------------------------
    # Add / Delete
    # ------------------------------------------------------------------

    async def add_tasks(self, task_list_id: str, titles: List[str]) -> List[Task]:
        task_list = await self.get_task_list(task_list_id)
        if not task_list:
            return []
        start = len(task_list.tasks)
        new_tasks = [
            Task(
                id=str(uuid4()),
                title=t.strip(),
                status=TaskStatus.PLANNED,
                order=start + i,
            )
            for i, t in enumerate(titles)
            if t.strip()
        ]
        updated_list = dataclasses.replace(task_list, tasks=task_list.tasks + new_tasks)
        await self._save_tasks(updated_list)
        return new_tasks

    async def delete_task(self, task_list_id: str, task_id: str) -> bool:
        task_list = await self.get_task_list(task_list_id)
        if not task_list:
            return False
        before = len(task_list.tasks)
        new_tasks = [t for t in task_list.tasks if t.id != task_id]
        if len(new_tasks) == before:
            return False
        await self._save_tasks(dataclasses.replace(task_list, tasks=new_tasks))
        return True

    async def increment_retry(self, task_list_id: str, task_id: str) -> Optional[Task]:
        """Agent bounded retry — returns None at the ceiling."""
        task_list = await self.get_task_list(task_list_id)
        if not task_list:
            return None
        updated: Task | None = None
        for task in task_list.tasks:
            if task.id == task_id:
                if task.retry_count >= task_list.max_retries:
                    return None
                updated = dataclasses.replace(
                    task,
                    retry_count=task.retry_count + 1,
                    status=TaskStatus.IN_PROGRESS,
                )
                break
        if updated is None:
            return None
        new_list = dataclasses.replace(
            task_list,
            tasks=[updated if t.id == task_id else t for t in task_list.tasks],
        )
        await self._save_tasks(new_list)
        return updated

    async def force_retry(self, task_list_id: str, task_id: str) -> Optional[Task]:
        """User override — resets retry_count to 0 and sets in_progress."""
        task_list = await self.get_task_list(task_list_id)
        if not task_list:
            return None
        updated: Task | None = None
        for task in task_list.tasks:
            if task.id == task_id:
                updated = dataclasses.replace(
                    task, retry_count=0, status=TaskStatus.IN_PROGRESS, note=""
                )
                break
        if updated is None:
            return None
        new_list = dataclasses.replace(
            task_list,
            tasks=[updated if t.id == task_id else t for t in task_list.tasks],
        )
        await self._save_tasks(new_list)
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
                updated = dataclasses.replace(task, title=title.strip())
                break
        if updated is None:
            return None
        new_list = dataclasses.replace(
            task_list,
            tasks=[updated if t.id == task_id else t for t in task_list.tasks],
        )
        await self._save_tasks(new_list)
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
        "note": t.note,
    }


def _row_to_task_list(row: object) -> TaskList:
    m = row._mapping  # type: ignore[union-attr]
    tasks_raw = m["tasks"]
    tasks_data: list = (
        json.loads(tasks_raw) if isinstance(tasks_raw, str) else (tasks_raw or [])
    )
    created = m.get("created_at")
    return TaskList(
        id=m["id"],
        conversation_id=m["conversation_id"],
        max_retries=m["max_retries"],
        agent_id=m.get("agent_id", ""),
        agent_label=m.get("agent_label", ""),
        parent_agent_id=m.get("parent_agent_id"),
        created_at=(
            created.isoformat() if hasattr(created, "isoformat") else (created or "")
        ),
        tasks=[
            Task(
                id=t["id"],
                title=t["title"],
                status=t.get("status", TaskStatus.PLANNED),
                order=t.get("order", 0),
                retry_count=t.get("retry_count", 0),
                note=t.get("note", ""),
            )
            for t in tasks_data
        ],
    )


__all__ = ["PgTaskStore"]
