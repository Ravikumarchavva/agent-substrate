from __future__ import annotations

import json
import time

from loamz.agents.features.automation.pipelines.model import PipelineDef
from loamz.agents.kernel.types.ids import new_id
from loamz.data.storage.sql import Database

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS adapter_pipelines (
        id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        name TEXT NOT NULL,
        description TEXT NOT NULL,
        definition_json TEXT NOT NULL,
        created_by TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE UNIQUE INDEX IF NOT EXISTS adapter_pipelines_tenant_name_idx ON adapter_pipelines (tenant_id, name);
    """,
]


class PipelinesStore:
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

    async def save(self, tenant_id: str, pipeline: PipelineDef) -> None:
        await self._init()
        now = time.time()
        pid = new_id()
        raw = json.dumps(pipeline.model_dump())
        async with self._db.transaction() as tx:
            await tx.execute(
                """
                INSERT INTO adapter_pipelines (id, tenant_id, name, description, definition_json, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (tenant_id, name) DO UPDATE SET
                    description = EXCLUDED.description,
                    definition_json = EXCLUDED.definition_json,
                    updated_at = EXCLUDED.updated_at
                """,
                pid,
                tenant_id,
                pipeline.name,
                pipeline.description,
                raw,
                pipeline.created_by,
                now,
                now,
            )

    async def load(self, tenant_id: str, name: str) -> PipelineDef | None:
        await self._init()
        async with self._db.transaction() as tx:
            r = await tx.fetchone(
                "SELECT definition_json FROM adapter_pipelines WHERE tenant_id = ? AND name = ?",
                tenant_id,
                name,
            )
        if not r:
            return None
        data = json.loads(str(r["definition_json"]))
        return PipelineDef(**data)

    async def list_all(self, tenant_id: str) -> list[PipelineDef]:
        await self._init()
        async with self._db.transaction() as tx:
            rows = await tx.fetchall(
                "SELECT definition_json FROM adapter_pipelines WHERE tenant_id = ? ORDER BY created_at DESC",
                tenant_id,
            )
        return [PipelineDef(**json.loads(str(r["definition_json"]))) for r in rows]

    async def delete(self, tenant_id: str, name: str) -> bool:
        await self._init()
        async with self._db.transaction() as tx:
            r = await tx.fetchone(
                "SELECT id FROM adapter_pipelines WHERE tenant_id = ? AND name = ?",
                tenant_id,
                name,
            )
            if not r:
                return False
            await tx.execute(
                "DELETE FROM adapter_pipelines WHERE tenant_id = ? AND name = ?",
                tenant_id,
                name,
            )
        return True
