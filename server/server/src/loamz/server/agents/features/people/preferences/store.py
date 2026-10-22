from __future__ import annotations

import json
import time

from loamz.engine.sql import Database, migrate
from loamz.server.agents.features.people.preferences.model import Preferences

COMPONENT = "people.preferences"
SCRIPTS = [
    """
    CREATE TABLE IF NOT EXISTS people_preferences (
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        body TEXT NOT NULL,
        updated_at DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (tenant_id, user_id)
    )
    """,
]


class PreferencesStore:
    """One row per (tenant, user). Every statement is keyed by both, so no query can reach another person's row."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self._ready = False

    async def _ensure(self) -> None:
        if not self._ready:
            await migrate(self._db, COMPONENT, SCRIPTS)
            self._ready = True

    async def get(self, tenant_id: str, user_id: str) -> Preferences | None:
        await self._ensure()
        async with self._db.transaction() as tx:
            row = await tx.fetchone(
                "SELECT body FROM people_preferences WHERE tenant_id = ? AND user_id = ?",
                tenant_id,
                user_id,
            )
        return None if row is None else Preferences.model_validate(json.loads(row["body"]))

    async def put(self, tenant_id: str, user_id: str, preferences: Preferences) -> None:
        await self._ensure()
        async with self._db.transaction() as tx:
            await tx.execute(
                "INSERT INTO people_preferences (tenant_id, user_id, body, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (tenant_id, user_id) DO UPDATE SET body = excluded.body, updated_at = excluded.updated_at",
                tenant_id,
                user_id,
                preferences.model_dump_json(),
                time.time(),
            )

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        """Remove a user's preferences, or (no user) the whole tenant's."""
        await self._ensure()
        async with self._db.transaction() as tx:
            if user_id is None:
                return await tx.execute(
                    "DELETE FROM people_preferences WHERE tenant_id = ?", tenant_id
                )
            return await tx.execute(
                "DELETE FROM people_preferences WHERE tenant_id = ? AND user_id = ?",
                tenant_id,
                user_id,
            )
