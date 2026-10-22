from __future__ import annotations

from loamz.engine.sql import Database, migrate

COMPONENT = "directory.pins"
SCRIPTS = [
    """
    CREATE TABLE IF NOT EXISTS directory_pins (
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        item_id TEXT NOT NULL,
        pinned_at DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (tenant_id, user_id, kind, item_id)
    )
    """,
]


class PinsStore:
    """One row per pinned agent or group; the limit counts across kinds."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self._ready = False

    async def _ensure(self) -> None:
        if not self._ready:
            await migrate(self._db, COMPONENT, SCRIPTS)
            self._ready = True

    async def count(self, tenant_id: str, user_id: str) -> int:
        await self._ensure()
        async with self._db.transaction() as tx:
            row = await tx.fetchone(
                "SELECT COUNT(*) AS c FROM directory_pins WHERE tenant_id = ? AND user_id = ?",
                tenant_id,
                user_id,
            )
        return int(row["c"])  # type: ignore[index]

    async def get(self, tenant_id: str, user_id: str, kind: str, item_id: str) -> float | None:
        await self._ensure()
        async with self._db.transaction() as tx:
            row = await tx.fetchone(
                "SELECT pinned_at FROM directory_pins "
                "WHERE tenant_id = ? AND user_id = ? AND kind = ? AND item_id = ?",
                tenant_id,
                user_id,
                kind,
                item_id,
            )
        return None if row is None else row["pinned_at"]

    async def add(self, tenant_id: str, user_id: str, kind: str, item_id: str, at: float) -> None:
        await self._ensure()
        async with self._db.transaction() as tx:
            await tx.execute(
                "INSERT INTO directory_pins (tenant_id, user_id, kind, item_id, pinned_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                tenant_id,
                user_id,
                kind,
                item_id,
                at,
            )

    async def remove(self, tenant_id: str, user_id: str, kind: str, item_id: str) -> None:
        await self._ensure()
        async with self._db.transaction() as tx:
            await tx.execute(
                "DELETE FROM directory_pins "
                "WHERE tenant_id = ? AND user_id = ? AND kind = ? AND item_id = ?",
                tenant_id,
                user_id,
                kind,
                item_id,
            )

    async def of_kind(self, tenant_id: str, user_id: str, kind: str) -> dict[str, float]:
        await self._ensure()
        async with self._db.transaction() as tx:
            rows = await tx.fetchall(
                "SELECT item_id, pinned_at FROM directory_pins "
                "WHERE tenant_id = ? AND user_id = ? AND kind = ?",
                tenant_id,
                user_id,
                kind,
            )
        return {r["item_id"]: r["pinned_at"] for r in rows}

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        await self._ensure()
        async with self._db.transaction() as tx:
            if user_id is None:
                return await tx.execute("DELETE FROM directory_pins WHERE tenant_id = ?", tenant_id)
            return await tx.execute(
                "DELETE FROM directory_pins WHERE tenant_id = ? AND user_id = ?",
                tenant_id,
                user_id,
            )
