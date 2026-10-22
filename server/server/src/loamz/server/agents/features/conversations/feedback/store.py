from __future__ import annotations

import time

from loamz.engine.agents.kernel.types.ids import new_id
from loamz.engine.sql import Database, migrate
from loamz.server.agents.features.conversations.feedback.model import FeedbackOut

COMPONENT = "conversations.feedback"
SCRIPTS = [
    """
    CREATE TABLE IF NOT EXISTS conversations_feedback (
        tenant_id TEXT NOT NULL,
        id TEXT NOT NULL,
        thread_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        for_id TEXT NOT NULL,
        value INTEGER NOT NULL,
        comment TEXT,
        created_at DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (tenant_id, id)
    );
    CREATE UNIQUE INDEX IF NOT EXISTS conversations_feedback_answer_idx
        ON conversations_feedback (tenant_id, thread_id, for_id);
    """,
]


class FeedbackStore:
    """One rating per answer; every statement is keyed by the tenant."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self._ready = False

    async def _ensure(self) -> None:
        if not self._ready:
            await migrate(self._db, COMPONENT, SCRIPTS)
            self._ready = True

    async def set(
        self,
        tenant_id: str,
        user_id: str,
        thread_id: str,
        for_id: str,
        value: int,
        comment: str | None,
    ) -> FeedbackOut | None:
        """Replace the answer's rating; 0 takes it back (returns None)."""
        await self._ensure()
        async with self._db.transaction() as tx:
            await tx.execute(
                "DELETE FROM conversations_feedback WHERE tenant_id = ? AND thread_id = ? AND for_id = ?",
                tenant_id,
                thread_id,
                for_id,
            )
            if value == 0:
                return None
            feedback_id = new_id()
            await tx.execute(
                "INSERT INTO conversations_feedback "
                "(tenant_id, id, thread_id, user_id, for_id, value, comment, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                tenant_id,
                feedback_id,
                thread_id,
                user_id,
                for_id,
                value,
                comment,
                time.time(),
            )
        return FeedbackOut(
            id=feedback_id, for_id=for_id, thread_id=thread_id, value=value, comment=comment
        )

    async def for_thread(self, tenant_id: str, thread_id: str) -> list[FeedbackOut]:
        await self._ensure()
        async with self._db.transaction() as tx:
            rows = await tx.fetchall(
                "SELECT id, for_id, thread_id, value, comment FROM conversations_feedback "
                "WHERE tenant_id = ? AND thread_id = ? ORDER BY created_at, id",
                tenant_id,
                thread_id,
            )
        return [
            FeedbackOut(
                id=r["id"],
                for_id=r["for_id"],
                thread_id=r["thread_id"],
                value=int(r["value"]),
                comment=r["comment"],
            )
            for r in rows
        ]

    async def delete_thread(self, tenant_id: str, thread_id: str) -> int:
        await self._ensure()
        async with self._db.transaction() as tx:
            return await tx.execute(
                "DELETE FROM conversations_feedback WHERE tenant_id = ? AND thread_id = ?",
                tenant_id,
                thread_id,
            )

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        await self._ensure()
        async with self._db.transaction() as tx:
            if user_id is None:
                return await tx.execute(
                    "DELETE FROM conversations_feedback WHERE tenant_id = ?", tenant_id
                )
            return await tx.execute(
                "DELETE FROM conversations_feedback WHERE tenant_id = ? AND user_id = ?",
                tenant_id,
                user_id,
            )
