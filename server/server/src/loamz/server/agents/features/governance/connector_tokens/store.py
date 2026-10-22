from __future__ import annotations

import time

from loamz.engine.sql import Database, migrate
from loamz.server.agents.features.governance.connector_tokens.model import TokenPush

COMPONENT = "governance.connector_tokens"
SCRIPTS = [
    """
    CREATE TABLE IF NOT EXISTS governance_connector_tokens (
        tenant_id TEXT NOT NULL,
        connector_type TEXT NOT NULL,
        project_id TEXT NOT NULL,
        pushed_by TEXT NOT NULL,
        access_token TEXT NOT NULL,
        refresh_token TEXT,
        token_expires_at BIGINT NOT NULL,
        expires_at DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (tenant_id, connector_type, project_id)
    )
    """,
]


class ConnectorTokenStore:
    """A short-lived cache keyed by tenant, connector and project; a row past `expires_at` is gone as far as readers go."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self._ready = False

    async def _ensure(self) -> None:
        if not self._ready:
            await migrate(self._db, COMPONENT, SCRIPTS)
            self._ready = True

    async def put(
        self, tenant_id: str, connector_type: str, pushed_by: str, token: TokenPush, ttl: float
    ) -> None:
        await self._ensure()
        async with self._db.transaction() as tx:
            await tx.execute(
                "DELETE FROM governance_connector_tokens WHERE expires_at <= ?", time.time()
            )
            await tx.execute(
                "INSERT INTO governance_connector_tokens (tenant_id, connector_type, project_id, "
                "pushed_by, access_token, refresh_token, token_expires_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (tenant_id, connector_type, project_id) "
                "DO UPDATE SET pushed_by = excluded.pushed_by, access_token = excluded.access_token, "
                "refresh_token = excluded.refresh_token, token_expires_at = excluded.token_expires_at, "
                "expires_at = excluded.expires_at",
                tenant_id,
                connector_type,
                token.project_id,
                pushed_by,
                token.access_token,
                token.refresh_token,
                token.expires_at,
                time.time() + ttl,
            )

    async def get(self, tenant_id: str, connector_type: str, project_id: str) -> TokenPush | None:
        await self._ensure()
        async with self._db.transaction() as tx:
            row = await tx.fetchone(
                "SELECT access_token, refresh_token, token_expires_at FROM governance_connector_tokens "
                "WHERE tenant_id = ? AND connector_type = ? AND project_id = ? AND expires_at > ?",
                tenant_id,
                connector_type,
                project_id,
                time.time(),
            )
        if row is None:
            return None
        return TokenPush(
            project_id=project_id,
            access_token=row["access_token"],
            refresh_token=row["refresh_token"],
            expires_at=row["token_expires_at"],
        )

    async def delete(self, tenant_id: str, connector_type: str, project_id: str) -> int:
        await self._ensure()
        async with self._db.transaction() as tx:
            return await tx.execute(
                "DELETE FROM governance_connector_tokens "
                "WHERE tenant_id = ? AND connector_type = ? AND project_id = ?",
                tenant_id,
                connector_type,
                project_id,
            )

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        """A tenant's tokens, or (with a user) the ones that person pushed."""
        await self._ensure()
        async with self._db.transaction() as tx:
            if user_id is None:
                return await tx.execute(
                    "DELETE FROM governance_connector_tokens WHERE tenant_id = ?", tenant_id
                )
            return await tx.execute(
                "DELETE FROM governance_connector_tokens WHERE tenant_id = ? AND pushed_by = ?",
                tenant_id,
                user_id,
            )
