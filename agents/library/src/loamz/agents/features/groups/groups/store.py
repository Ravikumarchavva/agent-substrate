from __future__ import annotations

import time
from dataclasses import dataclass

from loamz.agents.kernel.types.ids import new_id
from loamz.data.storage.sql import Database

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS groups (
        id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        name TEXT NOT NULL,
        avatar_key TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS groups_owner_idx ON groups (tenant_id, user_id, updated_at);
    """,
    """
    CREATE TABLE IF NOT EXISTS group_members (
        group_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        mode TEXT NOT NULL,
        PRIMARY KEY (group_id, agent_id)
    );
    """,
]


@dataclass(frozen=True, slots=True)
class GroupRecord:
    id: str
    tenant_id: str
    user_id: str
    name: str
    avatar_key: str | None
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class MemberRecord:
    group_id: str
    agent_id: str
    mode: str


class GroupsStore:
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

    async def list(self, tenant_id: str, user_id: str) -> list[GroupRecord]:
        await self._init()
        async with self._db.transaction() as tx:
            rows = await tx.fetchall(
                "SELECT id, tenant_id, user_id, name, avatar_key, created_at, updated_at "
                "FROM groups WHERE tenant_id = ? AND user_id = ? ORDER BY updated_at DESC",
                tenant_id,
                user_id,
            )
            return [
                GroupRecord(
                    id=str(r["id"]),
                    tenant_id=str(r["tenant_id"]),
                    user_id=str(r["user_id"]),
                    name=str(r["name"]),
                    avatar_key=str(r["avatar_key"]) if r["avatar_key"] else None,
                    created_at=float(r["created_at"]),
                    updated_at=float(r["updated_at"]),
                )
                for r in rows
            ]

    async def get(self, tenant_id: str, user_id: str, group_id: str) -> GroupRecord | None:
        await self._init()
        async with self._db.transaction() as tx:
            rows = await tx.fetchall(
                "SELECT id, tenant_id, user_id, name, avatar_key, created_at, updated_at "
                "FROM groups WHERE tenant_id = ? AND user_id = ? AND id = ?",
                tenant_id,
                user_id,
                group_id,
            )
            if not rows:
                return None
            r = rows[0]
            return GroupRecord(
                id=str(r["id"]),
                tenant_id=str(r["tenant_id"]),
                user_id=str(r["user_id"]),
                name=str(r["name"]),
                avatar_key=str(r["avatar_key"]) if r["avatar_key"] else None,
                created_at=float(r["created_at"]),
                updated_at=float(r["updated_at"]),
            )

    async def create(
        self, tenant_id: str, user_id: str, name: str, members: list[tuple[str, str]]
    ) -> GroupRecord:
        await self._init()
        group_id = new_id()
        now = time.time()
        async with self._db.transaction() as tx:
            await tx.execute(
                "INSERT INTO groups (id, tenant_id, user_id, name, avatar_key, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                group_id,
                tenant_id,
                user_id,
                name,
                None,
                now,
                now,
            )
            for agent_id, mode in members:
                await tx.execute(
                    "INSERT INTO group_members (group_id, agent_id, mode) VALUES (?, ?, ?)",
                    group_id,
                    agent_id,
                    mode,
                )
        return GroupRecord(
            id=group_id,
            tenant_id=tenant_id,
            user_id=user_id,
            name=name,
            avatar_key=None,
            created_at=now,
            updated_at=now,
        )

    async def update(
        self,
        tenant_id: str,
        user_id: str,
        group_id: str,
        *,
        name: str | None = None,
        avatar_key: str | None = None,
        clear_avatar: bool = False,
    ) -> GroupRecord | None:
        await self._init()
        now = time.time()
        async with self._db.transaction() as tx:
            if name is not None:
                await tx.execute(
                    "UPDATE groups SET name = ?, updated_at = ? WHERE tenant_id = ? AND user_id = ? AND id = ?",
                    name,
                    now,
                    tenant_id,
                    user_id,
                    group_id,
                )
            if avatar_key is not None:
                await tx.execute(
                    "UPDATE groups SET avatar_key = ?, updated_at = ? WHERE tenant_id = ? AND user_id = ? AND id = ?",
                    avatar_key,
                    now,
                    tenant_id,
                    user_id,
                    group_id,
                )
            elif clear_avatar:
                await tx.execute(
                    "UPDATE groups SET avatar_key = NULL, updated_at = ? WHERE tenant_id = ? AND user_id = ? AND id = ?",
                    now,
                    tenant_id,
                    user_id,
                    group_id,
                )
        return await self.get(tenant_id, user_id, group_id)

    async def delete(self, tenant_id: str, user_id: str, group_id: str) -> bool:
        await self._init()
        async with self._db.transaction() as tx:
            await tx.execute("DELETE FROM group_members WHERE group_id = ?", group_id)
            deleted = await tx.execute(
                "DELETE FROM groups WHERE tenant_id = ? AND user_id = ? AND id = ?",
                tenant_id,
                user_id,
                group_id,
            )
            return deleted > 0

    async def members(self, group_id: str) -> list[MemberRecord]:
        await self._init()
        async with self._db.transaction() as tx:
            rows = await tx.fetchall(
                "SELECT group_id, agent_id, mode FROM group_members WHERE group_id = ?",
                group_id,
            )
            return [
                MemberRecord(
                    group_id=str(r["group_id"]),
                    agent_id=str(r["agent_id"]),
                    mode=str(r["mode"]),
                )
                for r in rows
            ]

    async def add_member(self, group_id: str, agent_id: str, mode: str) -> None:
        await self._init()
        async with self._db.transaction() as tx:
            await tx.execute(
                "INSERT INTO group_members (group_id, agent_id, mode) VALUES (?, ?, ?) "
                "ON CONFLICT(group_id, agent_id) DO UPDATE SET mode = excluded.mode",
                group_id,
                agent_id,
                mode,
            )

    async def remove_member(self, group_id: str, agent_id: str) -> None:
        await self._init()
        async with self._db.transaction() as tx:
            await tx.execute(
                "DELETE FROM group_members WHERE group_id = ? AND agent_id = ?",
                group_id,
                agent_id,
            )

    async def set_member_mode(self, group_id: str, agent_id: str, mode: str) -> None:
        await self.add_member(group_id, agent_id, mode)

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        await self._init()
        async with self._db.transaction() as tx:
            if user_id is not None:
                rows = await tx.fetchall(
                    "SELECT id FROM groups WHERE tenant_id = ? AND user_id = ?",
                    tenant_id,
                    user_id,
                )
                for r in rows:
                    await tx.execute("DELETE FROM group_members WHERE group_id = ?", str(r["id"]))
                return await tx.execute(
                    "DELETE FROM groups WHERE tenant_id = ? AND user_id = ?", tenant_id, user_id
                )
            rows = await tx.fetchall("SELECT id FROM groups WHERE tenant_id = ?", tenant_id)
            for r in rows:
                await tx.execute("DELETE FROM group_members WHERE group_id = ?", str(r["id"]))
            return await tx.execute("DELETE FROM groups WHERE tenant_id = ?", tenant_id)
