from __future__ import annotations

import json
import time

from loamz.engine.agents.kernel.types.ids import new_id
from loamz.engine.sql import Database

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS triggers (
        id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        category TEXT NOT NULL,
        name TEXT NOT NULL,
        path TEXT,
        definition_json TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE UNIQUE INDEX IF NOT EXISTS triggers_tenant_cat_name_idx ON triggers (tenant_id, category, name);
    CREATE INDEX IF NOT EXISTS triggers_tenant_cat_path_idx ON triggers (tenant_id, category, path);
    """,
]


class TriggersStore:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._migrated = False

    async def _init(self) -> None:
        if not self._migrated:
            async with self._db.transaction() as tx:
                for ddl in SCHEMA:
                    for stmt in filter(None, (s.strip() for s in ddl.split(";"))):
                        await tx.execute(stmt)
            self._migrated = True

    async def save(
        self,
        tenant_id: str,
        category: str,
        name: str,
        definition: dict[str, object],
        path: str | None = None,
    ) -> None:
        await self._init()
        now = time.time()
        tid = new_id()
        raw = json.dumps(definition)
        async with self._db.transaction() as tx:
            await tx.execute(
                """
                INSERT INTO triggers (id, tenant_id, category, name, path, definition_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (tenant_id, category, name) DO UPDATE SET
                    path = EXCLUDED.path,
                    definition_json = EXCLUDED.definition_json,
                    updated_at = EXCLUDED.updated_at
                """,
                tid,
                tenant_id,
                category,
                name,
                path,
                raw,
                now,
                now,
            )

    async def delete(self, tenant_id: str, category: str, name: str) -> bool:
        await self._init()
        async with self._db.transaction() as tx:
            r = await tx.fetchone(
                "SELECT id FROM triggers WHERE tenant_id = ? AND category = ? AND name = ?",
                tenant_id,
                category,
                name,
            )
            if not r:
                return False
            await tx.execute(
                "DELETE FROM triggers WHERE tenant_id = ? AND category = ? AND name = ?",
                tenant_id,
                category,
                name,
            )
        return True

    async def delete_by_path(self, tenant_id: str, category: str, path: str) -> bool:
        await self._init()
        clean_path = path.lstrip("/")
        async with self._db.transaction() as tx:
            r = await tx.fetchone(
                "SELECT id FROM triggers WHERE tenant_id = ? AND category = ? AND path = ?",
                tenant_id,
                category,
                clean_path,
            )
            if not r:
                return False
            await tx.execute(
                "DELETE FROM triggers WHERE tenant_id = ? AND category = ? AND path = ?",
                tenant_id,
                category,
                clean_path,
            )
        return True

    async def get_by_path(
        self, tenant_id: str, category: str, path: str
    ) -> dict[str, object] | None:
        await self._init()
        clean_path = path.lstrip("/")
        async with self._db.transaction() as tx:
            r = await tx.fetchone(
                "SELECT definition_json FROM triggers WHERE tenant_id = ? AND category = ? AND path = ?",
                tenant_id,
                category,
                clean_path,
            )
        if not r:
            return None
        return json.loads(str(r["definition_json"]))

    async def list(self, tenant_id: str, category: str) -> list[dict[str, object]]:
        await self._init()
        async with self._db.transaction() as tx:
            rows = await tx.fetchall(
                "SELECT definition_json FROM triggers WHERE tenant_id = ? AND category = ? ORDER BY created_at DESC",
                tenant_id,
                category,
            )
        return [json.loads(str(r["definition_json"])) for r in rows]
