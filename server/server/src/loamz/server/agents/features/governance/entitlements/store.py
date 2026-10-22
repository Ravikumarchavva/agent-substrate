from __future__ import annotations

import json
import time

from loamz.engine.sql import Database, migrate
from loamz.server.agents.features.governance.entitlements.model import Entitlements

COMPONENT = "governance.entitlements"
SCRIPTS = [
    """
    CREATE TABLE IF NOT EXISTS governance_entitlements (
        tenant_id TEXT PRIMARY KEY,
        daily_message_limit INTEGER,
        features_json TEXT NOT NULL,
        updated_by TEXT NOT NULL,
        updated_at DOUBLE PRECISION NOT NULL
    )
    """,
]


class EntitlementStore:
    """One row per tenant. Absence of a row, or a NULL limit, means unlimited."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self._ready = False

    async def _ensure(self) -> None:
        if not self._ready:
            await migrate(self._db, COMPONENT, SCRIPTS)
            self._ready = True

    async def get(self, tenant_id: str) -> Entitlements | None:
        await self._ensure()
        async with self._db.transaction() as tx:
            row = await tx.fetchone(
                "SELECT daily_message_limit, features_json FROM governance_entitlements "
                "WHERE tenant_id = ?",
                tenant_id,
            )
        if row is None:
            return None
        return Entitlements(
            tenant_id=tenant_id,
            daily_message_limit=row["daily_message_limit"],
            features=json.loads(row["features_json"]),
        )

    async def put(self, entitlements: Entitlements, updated_by: str) -> None:
        """Replace the tenant's entitlements (idempotent)."""
        await self._ensure()
        async with self._db.transaction() as tx:
            await tx.execute(
                "INSERT INTO governance_entitlements "
                "(tenant_id, daily_message_limit, features_json, updated_by, updated_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT (tenant_id) DO UPDATE SET "
                "daily_message_limit = excluded.daily_message_limit, "
                "features_json = excluded.features_json, updated_by = excluded.updated_by, "
                "updated_at = excluded.updated_at",
                entitlements.tenant_id,
                entitlements.daily_message_limit,
                json.dumps(entitlements.features, default=str),
                updated_by,
                time.time(),
            )
