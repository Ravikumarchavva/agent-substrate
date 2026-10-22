from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

from loamz.engine.sql import Database, migrate

COMPONENT = "conversations.shares"
SCRIPTS = [
    """
    CREATE TABLE IF NOT EXISTS conversations_shares (
        token TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        thread_id TEXT NOT NULL,
        name TEXT,
        created_at DOUBLE PRECISION NOT NULL
    );
    CREATE UNIQUE INDEX IF NOT EXISTS conversations_shares_thread_idx
        ON conversations_shares (tenant_id, thread_id);
    """,
]


@dataclass(frozen=True)
class Share:
    token: str
    tenant_id: str
    thread_id: str
    name: str | None


class SharesStore:
    """A public read-only link to a conversation. The token is the whole credential (32 random bytes), so it is
    looked up by token alone; everything else is keyed by `(tenant_id, thread_id)`."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self._ready = False

    async def _ensure(self) -> None:
        if not self._ready:
            await migrate(self._db, COMPONENT, SCRIPTS)
            self._ready = True

    @staticmethod
    def _share(row) -> Share:
        return Share(row["token"], row["tenant_id"], row["thread_id"], row["name"])

    async def for_thread(self, tenant_id: str, thread_id: str) -> Share | None:
        await self._ensure()
        async with self._db.transaction() as tx:
            row = await tx.fetchone(
                "SELECT token, tenant_id, thread_id, name FROM conversations_shares "
                "WHERE tenant_id = ? AND thread_id = ?",
                tenant_id,
                thread_id,
            )
        return None if row is None else self._share(row)

    async def by_token(self, token: str) -> Share | None:
        await self._ensure()
        async with self._db.transaction() as tx:
            row = await tx.fetchone(
                "SELECT token, tenant_id, thread_id, name FROM conversations_shares WHERE token = ?",
                token,
            )
        return None if row is None else self._share(row)

    async def create(self, tenant_id: str, thread_id: str, name: str | None) -> Share:
        """The thread's link, made now or already there (idempotent)."""
        await self._ensure()
        async with self._db.transaction() as tx:
            await tx.execute(
                "INSERT INTO conversations_shares (token, tenant_id, thread_id, name, created_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                secrets.token_urlsafe(32),
                tenant_id,
                thread_id,
                name,
                time.time(),
            )
        share = await self.for_thread(tenant_id, thread_id)
        assert share is not None
        return share

    async def delete_thread(self, tenant_id: str, thread_id: str) -> int:
        await self._ensure()
        async with self._db.transaction() as tx:
            return await tx.execute(
                "DELETE FROM conversations_shares WHERE tenant_id = ? AND thread_id = ?",
                tenant_id,
                thread_id,
            )

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        """A tenant's links; a user's own are removed with their threads (`delete_thread`), so only the whole
        tenant is erased here."""
        if user_id is not None:
            return 0
        await self._ensure()
        async with self._db.transaction() as tx:
            return await tx.execute(
                "DELETE FROM conversations_shares WHERE tenant_id = ?", tenant_id
            )
