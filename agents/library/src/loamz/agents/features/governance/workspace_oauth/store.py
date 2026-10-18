from __future__ import annotations

import time

from loamz.data.storage.sql import Database, migrate

COMPONENT = "governance.workspace_oauth"
SCRIPTS = [
    """
    CREATE TABLE IF NOT EXISTS governance_workspace_tokens (
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        access_token TEXT NOT NULL,
        expires_at DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (tenant_id, user_id)
    )
    """,
]


class WorkspaceTokenStore:
    """One cached access token per (tenant, user)."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self._ready = False

    async def _ensure(self) -> None:
        if not self._ready:
            await migrate(self._db, COMPONENT, SCRIPTS)
            self._ready = True

    async def put(self, tenant_id: str, user_id: str, token: str, ttl: float) -> None:
        await self._ensure()
        async with self._db.transaction() as tx:
            await tx.execute(
                "DELETE FROM governance_workspace_tokens WHERE expires_at <= ?", time.time()
            )
            await tx.execute(
                "INSERT INTO governance_workspace_tokens (tenant_id, user_id, access_token, expires_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (tenant_id, user_id) DO UPDATE SET "
                "access_token = excluded.access_token, expires_at = excluded.expires_at",
                tenant_id,
                user_id,
                token,
                time.time() + ttl,
            )

    async def get(self, tenant_id: str, user_id: str) -> str | None:
        await self._ensure()
        async with self._db.transaction() as tx:
            row = await tx.fetchone(
                "SELECT access_token FROM governance_workspace_tokens "
                "WHERE tenant_id = ? AND user_id = ? AND expires_at > ?",
                tenant_id,
                user_id,
                time.time(),
            )
        return None if row is None else row["access_token"]

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        await self._ensure()
        async with self._db.transaction() as tx:
            if user_id is None:
                return await tx.execute(
                    "DELETE FROM governance_workspace_tokens WHERE tenant_id = ?", tenant_id
                )
            return await tx.execute(
                "DELETE FROM governance_workspace_tokens WHERE tenant_id = ? AND user_id = ?",
                tenant_id,
                user_id,
            )
