from __future__ import annotations

import json
import time
from typing import Any

from loamz.agents.features.directory.agents.model import AgentProfile
from loamz.data.storage.sql import Database, Row, Tx, migrate

COMPONENT = "directory.agents"
SCRIPTS = [
    """
    CREATE TABLE IF NOT EXISTS directory_agents (
        tenant_id TEXT NOT NULL,
        id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        name TEXT NOT NULL,
        role TEXT NOT NULL,
        instructions TEXT NOT NULL,
        allowed_tools TEXT,
        model TEXT,
        avatar_key TEXT,
        created_at DOUBLE PRECISION NOT NULL,
        updated_at DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (tenant_id, id)
    );
    CREATE INDEX IF NOT EXISTS directory_agents_owner_idx
        ON directory_agents (tenant_id, user_id, created_at, id);
    CREATE TABLE IF NOT EXISTS directory_contacts (
        tenant_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        contact_id TEXT NOT NULL,
        note TEXT NOT NULL,
        PRIMARY KEY (tenant_id, agent_id, contact_id)
    );
    """,
]

_COLUMNS = (
    "id, tenant_id, user_id, name, role, instructions, allowed_tools, model, avatar_key, created_at"
)


def _profile(row: Row) -> AgentProfile:
    tools = row["allowed_tools"]
    return AgentProfile(
        id=row["id"],
        tenant_id=row["tenant_id"],
        user_id=row["user_id"],
        name=row["name"],
        role=row["role"],
        instructions=row["instructions"],
        allowed_tools=None if tools is None else tuple(json.loads(tools)),
        model=row["model"],
        avatar_key=row["avatar_key"],
        created_at=row["created_at"],
    )


class AgentsStore:
    """Agent profiles and contacts. Every statement is keyed by the tenant, and a person's reads by the user too."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self._ready = False

    async def _ensure(self) -> None:
        if not self._ready:
            await migrate(self._db, COMPONENT, SCRIPTS)
            self._ready = True

    async def _tx(self) -> Any:
        await self._ensure()
        return self._db.transaction()

    async def insert(self, profile: AgentProfile) -> None:
        async with await self._tx() as tx:
            await tx.execute(
                f"INSERT INTO directory_agents ({_COLUMNS}, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                profile.id,
                profile.tenant_id,
                profile.user_id,
                profile.name,
                profile.role,
                profile.instructions,
                None if profile.allowed_tools is None else json.dumps(profile.allowed_tools),
                profile.model,
                profile.avatar_key,
                profile.created_at,
                profile.created_at,
            )

    async def get(self, tenant_id: str, user_id: str, agent_id: str) -> AgentProfile | None:
        async with await self._tx() as tx:
            row = await tx.fetchone(
                f"SELECT {_COLUMNS} FROM directory_agents "
                "WHERE tenant_id = ? AND user_id = ? AND id = ?",
                tenant_id,
                user_id,
                agent_id,
            )
        return None if row is None else _profile(row)

    async def list(self, tenant_id: str, user_id: str) -> list[AgentProfile]:
        async with await self._tx() as tx:
            rows = await tx.fetchall(
                f"SELECT {_COLUMNS} FROM directory_agents "
                "WHERE tenant_id = ? AND user_id = ? ORDER BY created_at, id",
                tenant_id,
                user_id,
            )
        return [_profile(r) for r in rows]

    async def many(self, tenant_id: str, user_id: str, ids: list[str]) -> list[AgentProfile]:
        """The caller's own agents among `ids`, oldest first."""
        if not ids:
            return []
        marks = ", ".join("?" for _ in ids)
        async with await self._tx() as tx:
            rows = await tx.fetchall(
                f"SELECT {_COLUMNS} FROM directory_agents "
                f"WHERE tenant_id = ? AND user_id = ? AND id IN ({marks}) ORDER BY created_at, id",
                tenant_id,
                user_id,
                *ids,
            )
        return [_profile(r) for r in rows]

    async def update(self, profile: AgentProfile) -> None:
        async with await self._tx() as tx:
            await tx.execute(
                "UPDATE directory_agents SET name = ?, role = ?, instructions = ?, allowed_tools = ?, "
                "model = ?, avatar_key = ?, updated_at = ? WHERE tenant_id = ? AND user_id = ? AND id = ?",
                profile.name,
                profile.role,
                profile.instructions,
                None if profile.allowed_tools is None else json.dumps(profile.allowed_tools),
                profile.model,
                profile.avatar_key,
                time.time(),
                profile.tenant_id,
                profile.user_id,
                profile.id,
            )

    async def delete(self, tenant_id: str, user_id: str, agent_id: str) -> bool:
        async with await self._tx() as tx:
            await tx.execute(
                "DELETE FROM directory_contacts WHERE tenant_id = ? AND (agent_id = ? OR contact_id = ?)",
                tenant_id,
                agent_id,
                agent_id,
            )
            return bool(
                await tx.execute(
                    "DELETE FROM directory_agents WHERE tenant_id = ? AND user_id = ? AND id = ?",
                    tenant_id,
                    user_id,
                    agent_id,
                )
            )

    # ── contacts ────────────────────────────────────────────────────────────
    async def contacts(self, tenant_id: str, agent_id: str) -> list[tuple[AgentProfile, str]]:
        """The agents `agent_id` may message with the owner's note on each, oldest first."""
        async with await self._tx() as tx:
            rows = await tx.fetchall(
                "SELECT a.id, a.tenant_id, a.user_id, a.name, a.role, a.instructions, a.allowed_tools, "
                "a.model, a.avatar_key, a.created_at, c.note FROM directory_contacts c "
                "JOIN directory_agents a ON a.tenant_id = c.tenant_id AND a.id = c.contact_id "
                "WHERE c.tenant_id = ? AND c.agent_id = ? ORDER BY a.created_at, a.id",
                tenant_id,
                agent_id,
            )
        return [(_profile(r), r["note"]) for r in rows]

    async def set_contacts(
        self, tenant_id: str, agent_id: str, contacts: dict[str, str]
    ) -> None:
        """Replace the whole allow-list in one transaction."""
        async with await self._tx() as tx:
            await self._replace_contacts(tx, tenant_id, agent_id, contacts)

    @staticmethod
    async def _replace_contacts(
        tx: Tx, tenant_id: str, agent_id: str, contacts: dict[str, str]
    ) -> None:
        await tx.execute(
            "DELETE FROM directory_contacts WHERE tenant_id = ? AND agent_id = ?",
            tenant_id,
            agent_id,
        )
        for contact_id, note in contacts.items():
            await tx.execute(
                "INSERT INTO directory_contacts (tenant_id, agent_id, contact_id, note) "
                "VALUES (?, ?, ?, ?)",
                tenant_id,
                agent_id,
                contact_id,
                note,
            )

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        """Remove a user's agents (and their contacts), or (no user) the whole tenant's."""
        async with await self._tx() as tx:
            if user_id is None:
                await tx.execute("DELETE FROM directory_contacts WHERE tenant_id = ?", tenant_id)
                return await tx.execute(
                    "DELETE FROM directory_agents WHERE tenant_id = ?", tenant_id
                )
            await tx.execute(
                "DELETE FROM directory_contacts WHERE tenant_id = ? AND agent_id IN "
                "(SELECT id FROM directory_agents WHERE tenant_id = ? AND user_id = ?)",
                tenant_id,
                tenant_id,
                user_id,
            )
            return await tx.execute(
                "DELETE FROM directory_agents WHERE tenant_id = ? AND user_id = ?",
                tenant_id,
                user_id,
            )
