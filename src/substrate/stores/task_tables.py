"""Tasks — each agent's Kanban board, kept in the store's database.

``Tasks`` is the one implementation of ``TaskStore`` (``stores/tasks.py``). A board belongs to a
``(conversation, agent, branch)``; creating one for a key that has a board replaces it, in one transaction, so there
is never a moment with two (and the unique key makes that true across processes, not just within one).

Every change to a task is a single conditional ``UPDATE`` — the retry bound is part of the statement, not a check made
before it — so two workers racing to retry the last allowed attempt cannot both get it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, TypeVar
from uuid import uuid4

from substrate.stores.database import Row, Tx
from substrate.stores.tasks import Task, TaskList, TaskStatus

if TYPE_CHECKING:
    from substrate.stores.store import Store

T = TypeVar("T")

SCHEMA = [
    """
CREATE TABLE IF NOT EXISTS task_boards (
    seq {pk},
    id TEXT NOT NULL UNIQUE,
    conversation_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    agent_label TEXT NOT NULL,
    parent_agent_id TEXT,
    branch_id TEXT NOT NULL,
    max_retries INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (conversation_id, agent_id, branch_id)
);

CREATE TABLE IF NOT EXISTS task_items (
    seq {pk},
    id TEXT NOT NULL UNIQUE,
    board_id TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    position INTEGER NOT NULL,
    retry_count INTEGER NOT NULL,
    note TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS task_items_board_idx ON task_items (board_id);
"""
]

_TASK = "id, title, status, position, retry_count, note"


def _task(row: Row) -> Task:
    return Task(
        id=row["id"],
        title=row["title"],
        status=TaskStatus(row["status"]),
        order=row["position"],
        retry_count=row["retry_count"],
        note=row["note"],
    )


def _titles(titles: list[str]) -> list[str]:
    return [t.strip() for t in titles if t.strip()]


async def _board(tx: Tx, row: Row) -> TaskList:
    items = await tx.fetchall(f"SELECT {_TASK} FROM task_items WHERE board_id = ? ORDER BY position, seq", row["id"])
    return TaskList(
        id=row["id"],
        conversation_id=row["conversation_id"],
        tasks=[_task(i) for i in items],
        max_retries=row["max_retries"],
        agent_id=row["agent_id"],
        agent_label=row["agent_label"],
        parent_agent_id=row["parent_agent_id"],
        branch_id=row["branch_id"],
        created_at=row["created_at"],
    )


async def _read_task(tx: Tx, board_id: str, task_id: str) -> Task | None:
    row = await tx.fetchone(f"SELECT {_TASK} FROM task_items WHERE board_id = ? AND id = ?", board_id, task_id)
    return _task(row) if row else None


class Tasks:
    """The ``TaskStore`` of a ``Store``: ``store.tasks``."""

    def __init__(self, store: Store) -> None:
        self._store = store

    @property
    def store(self) -> Store:
        """The store these boards live in — for a host that opened it and has to close it."""
        return self._store

    async def _run(self, fn: Callable[[Tx], Awaitable[T]]) -> T:
        return await self._store.run(fn)

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
        board_id = str(uuid4())
        created_at = datetime.now(timezone.utc).isoformat()

        async def op(tx: Tx) -> TaskList:
            await tx.lock(f"tasks:{conversation_id}:{agent_id}:{branch_id}")
            await tx.execute(
                "DELETE FROM task_items WHERE board_id IN "
                "(SELECT id FROM task_boards WHERE conversation_id = ? AND agent_id = ? AND branch_id = ?)",
                conversation_id,
                agent_id,
                branch_id,
            )
            await tx.execute(
                "DELETE FROM task_boards WHERE conversation_id = ? AND agent_id = ? AND branch_id = ?",
                conversation_id,
                agent_id,
                branch_id,
            )
            await tx.execute(
                "INSERT INTO task_boards (id, conversation_id, agent_id, agent_label, parent_agent_id, branch_id, "
                "max_retries, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                board_id,
                conversation_id,
                agent_id,
                agent_label,
                parent_agent_id,
                branch_id,
                max_retries,
                created_at,
            )
            for position, title in enumerate(_titles(task_titles)):
                await tx.execute(
                    "INSERT INTO task_items (id, board_id, title, status, position, retry_count, note) "
                    "VALUES (?, ?, ?, ?, ?, 0, '')",
                    str(uuid4()),
                    board_id,
                    title,
                    TaskStatus.PLANNED.value,
                    position,
                )
            row = await tx.fetchone("SELECT * FROM task_boards WHERE id = ?", board_id)
            return await _board(tx, row)

        return await self._run(op)

    async def get_task_list(self, task_list_id: str) -> TaskList | None:
        async def op(tx: Tx) -> TaskList | None:
            row = await tx.fetchone("SELECT * FROM task_boards WHERE id = ?", task_list_id)
            return await _board(tx, row) if row else None

        return await self._run(op)

    async def get_boards_by_conversation(self, conversation_id: str, branch_id: str = "main") -> list[TaskList]:
        async def op(tx: Tx) -> list[TaskList]:
            rows = await tx.fetchall(
                "SELECT * FROM task_boards WHERE conversation_id = ? AND branch_id = ? ORDER BY created_at, seq",
                conversation_id,
                branch_id,
            )
            return [await _board(tx, row) for row in rows]

        return await self._run(op)

    async def get_by_conversation(self, conversation_id: str, branch_id: str = "main") -> TaskList | None:
        """The root board (``agent_id == ""``), else the earliest board for this conversation on this branch."""
        boards = await self.get_boards_by_conversation(conversation_id, branch_id)
        return next((b for b in boards if b.agent_id == ""), boards[0] if boards else None)

    async def settle_conversation(self, conversation_id: str) -> list[TaskList]:
        """Flip any lingering ``in_progress`` task on the main branch to ``succeeded`` when a run ends, so the board
        stops spinning. Returns the boards that changed."""

        async def op(tx: Tx) -> list[TaskList]:
            rows = await tx.fetchall(
                "SELECT * FROM task_boards WHERE conversation_id = ? AND branch_id = 'main' AND id IN "
                "(SELECT board_id FROM task_items WHERE status = ?) ORDER BY created_at, seq",
                conversation_id,
                TaskStatus.IN_PROGRESS.value,
            )
            for row in rows:
                await tx.execute(
                    "UPDATE task_items SET status = ? WHERE board_id = ? AND status = ?",
                    TaskStatus.SUCCEEDED.value,
                    row["id"],
                    TaskStatus.IN_PROGRESS.value,
                )
            return [await _board(tx, row) for row in rows]

        return await self._run(op)

    async def update_status(self, task_list_id: str, task_id: str, status: TaskStatus, note: str = "") -> Task | None:
        async def op(tx: Tx) -> Task | None:
            changed = await tx.execute(
                "UPDATE task_items SET status = ?, note = CASE WHEN ? <> '' THEN ? ELSE note END "
                "WHERE board_id = ? AND id = ?",
                status.value,
                note,
                note,
                task_list_id,
                task_id,
            )
            return await _read_task(tx, task_list_id, task_id) if changed else None

        return await self._run(op)

    async def add_tasks(self, task_list_id: str, titles: list[str]) -> list[Task]:
        async def op(tx: Tx) -> list[Task]:
            await tx.lock(f"task_board:{task_list_id}")
            if await tx.fetchone("SELECT 1 FROM task_boards WHERE id = ?", task_list_id) is None:
                return []
            row = await tx.fetchone("SELECT COALESCE(MAX(position) + 1, 0) AS next FROM task_items WHERE board_id = ?", task_list_id)
            added: list[Task] = []
            for offset, title in enumerate(_titles(titles)):
                task = Task(id=str(uuid4()), title=title, status=TaskStatus.PLANNED, order=row["next"] + offset)
                await tx.execute(
                    "INSERT INTO task_items (id, board_id, title, status, position, retry_count, note) "
                    "VALUES (?, ?, ?, ?, ?, 0, '')",
                    task.id,
                    task_list_id,
                    task.title,
                    task.status.value,
                    task.order,
                )
                added.append(task)
            return added

        return await self._run(op)

    async def delete_task(self, task_list_id: str, task_id: str) -> bool:
        async def op(tx: Tx) -> bool:
            return await tx.execute("DELETE FROM task_items WHERE board_id = ? AND id = ?", task_list_id, task_id) > 0

        return await self._run(op)

    async def increment_retry(self, task_list_id: str, task_id: str) -> Task | None:
        """Agent bounded retry: count it and move to in_progress; ``None`` once the board's maximum is reached.

        The bound is in the ``WHERE`` clause, so of any number of workers retrying together only as many succeed as
        there are attempts left."""

        async def op(tx: Tx) -> Task | None:
            changed = await tx.execute(
                "UPDATE task_items SET retry_count = retry_count + 1, status = ? "
                "WHERE board_id = ? AND id = ? AND retry_count < (SELECT max_retries FROM task_boards WHERE id = ?)",
                TaskStatus.IN_PROGRESS.value,
                task_list_id,
                task_id,
                task_list_id,
            )
            return await _read_task(tx, task_list_id, task_id) if changed else None

        return await self._run(op)

    async def force_retry(self, task_list_id: str, task_id: str) -> Task | None:
        """User override: reset the count and set in_progress."""

        async def op(tx: Tx) -> Task | None:
            changed = await tx.execute(
                "UPDATE task_items SET retry_count = 0, status = ?, note = '' WHERE board_id = ? AND id = ?",
                TaskStatus.IN_PROGRESS.value,
                task_list_id,
                task_id,
            )
            return await _read_task(tx, task_list_id, task_id) if changed else None

        return await self._run(op)

    async def update_task_title(self, task_list_id: str, task_id: str, title: str) -> Task | None:
        async def op(tx: Tx) -> Task | None:
            changed = await tx.execute(
                "UPDATE task_items SET title = ? WHERE board_id = ? AND id = ?", title.strip(), task_list_id, task_id
            )
            return await _read_task(tx, task_list_id, task_id) if changed else None

        return await self._run(op)


__all__ = ["SCHEMA", "Tasks"]
