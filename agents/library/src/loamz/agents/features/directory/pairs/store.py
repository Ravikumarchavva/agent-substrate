from __future__ import annotations

import time
from dataclasses import dataclass

from loamz.agents.features.directory.agents import store as agents_store
from loamz.agents.kernel.types.ids import new_id
from loamz.data.storage.sql import Database, Row, migrate

COMPONENT = "directory.pairs"
SCRIPTS = [
    """
    CREATE TABLE IF NOT EXISTS directory_pairs (
        tenant_id TEXT NOT NULL,
        id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        agent_a TEXT NOT NULL,
        agent_b TEXT NOT NULL,
        created_at DOUBLE PRECISION NOT NULL,
        last_at DOUBLE PRECISION NOT NULL,
        last_text TEXT NOT NULL,
        last_sender TEXT,
        exchanges INTEGER NOT NULL,
        PRIMARY KEY (tenant_id, id),
        UNIQUE (tenant_id, user_id, agent_a, agent_b)
    );
    CREATE INDEX IF NOT EXISTS directory_pairs_a_idx ON directory_pairs (tenant_id, user_id, agent_a, last_at);
    CREATE INDEX IF NOT EXISTS directory_pairs_b_idx ON directory_pairs (tenant_id, user_id, agent_b, last_at);
    """,
]
_COLUMNS = "id, tenant_id, user_id, agent_a, agent_b, created_at, last_at, last_text, last_sender, exchanges"


@dataclass(frozen=True)
class Pair:
    """Two of a user's agents that have talked: one row however many times and whichever way they asked. What was said
    is the runtime's channel `pair/<id>`; this row lists them (latest line, count)."""

    id: str
    tenant_id: str
    user_id: str
    agent_a: str
    agent_b: str
    created_at: float
    last_at: float
    last_text: str
    last_sender: str | None
    exchanges: int

    @property
    def channel(self) -> str:
        return f"pair/{self.id}"

    def other(self, agent_id: str) -> str:
        return self.agent_b if self.agent_a == agent_id else self.agent_a


def _pair(row: Row) -> Pair:
    return Pair(**{k: row[k] for k in Pair.__dataclass_fields__})


class PairsStore:
    def __init__(self, database: Database) -> None:
        self._db = database
        self._ready = False

    async def _ensure(self) -> None:
        if not self._ready:
            await migrate(self._db, agents_store.COMPONENT, agents_store.SCRIPTS)  # the join target
            await migrate(self._db, COMPONENT, SCRIPTS)
            self._ready = True

    async def get_or_create(self, tenant_id: str, user_id: str, low: str, high: str) -> Pair:
        await self._ensure()
        now = time.time()
        async with self._db.transaction() as tx:
            await tx.execute(
                f"INSERT INTO directory_pairs ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, '', NULL, 0) "
                "ON CONFLICT DO NOTHING",
                new_id(),
                tenant_id,
                user_id,
                low,
                high,
                now,
                now,
            )
            row = await tx.fetchone(
                f"SELECT {_COLUMNS} FROM directory_pairs "
                "WHERE tenant_id = ? AND user_id = ? AND agent_a = ? AND agent_b = ?",
                tenant_id,
                user_id,
                low,
                high,
            )
        assert row is not None
        return _pair(row)

    async def touch(self, pair: Pair, *, text: str, sender: str, asked: bool) -> None:
        await self._ensure()
        async with self._db.transaction() as tx:
            await tx.execute(
                "UPDATE directory_pairs SET last_at = ?, last_text = ?, last_sender = ?, "
                "exchanges = exchanges + ? WHERE tenant_id = ? AND id = ?",
                time.time(),
                text,
                sender,
                1 if asked else 0,
                pair.tenant_id,
                pair.id,
            )

    async def of_agent(
        self,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        *,
        q: str = "",
        before: float | None = None,
        limit: int = 30,
    ) -> list[tuple[Pair, str, str | None]]:
        """The agent's pairs, newest first, each with the other agent's name and picture (keyset on time)."""
        await self._ensure()
        where = [
            "p.tenant_id = ?",
            "p.user_id = ?",
            "(p.agent_a = ? OR p.agent_b = ?)",
        ]
        params: list[object] = [tenant_id, user_id, agent_id, agent_id]
        if before is not None:
            where.append("p.last_at < ?")
            params.append(before)
        if q.strip():
            where.append("LOWER(o.name) LIKE ?")
            params.append(f"%{q.strip().lower()}%")
        async with self._db.transaction() as tx:
            rows = await tx.fetchall(
                "SELECT "
                + ", ".join(f"p.{c}" for c in _COLUMNS.split(", "))
                + ", o.name AS other_name, o.avatar_key AS other_avatar FROM directory_pairs p "
                "JOIN directory_agents o ON o.tenant_id = p.tenant_id AND o.id = "
                "(CASE WHEN p.agent_a = ? THEN p.agent_b ELSE p.agent_a END) "
                "WHERE " + " AND ".join(where) + " ORDER BY p.last_at DESC, p.id DESC LIMIT ?",
                agent_id,
                *params,
                limit,
            )
        return [(_pair(r), r["other_name"], r["other_avatar"]) for r in rows]

    async def get_of(
        self, tenant_id: str, user_id: str, agent_id: str, pair_id: str
    ) -> Pair | None:
        await self._ensure()
        async with self._db.transaction() as tx:
            row = await tx.fetchone(
                f"SELECT {_COLUMNS} FROM directory_pairs WHERE tenant_id = ? AND user_id = ? "
                "AND id = ? AND (agent_a = ? OR agent_b = ?)",
                tenant_id,
                user_id,
                pair_id,
                agent_id,
                agent_id,
            )
        return None if row is None else _pair(row)

    async def with_agent(self, tenant_id: str, agent_id: str) -> list[Pair]:
        await self._ensure()
        async with self._db.transaction() as tx:
            rows = await tx.fetchall(
                f"SELECT {_COLUMNS} FROM directory_pairs "
                "WHERE tenant_id = ? AND (agent_a = ? OR agent_b = ?)",
                tenant_id,
                agent_id,
                agent_id,
            )
        return [_pair(r) for r in rows]

    async def delete(self, tenant_id: str, ids: list[str]) -> None:
        if not ids:
            return
        await self._ensure()
        marks = ", ".join("?" for _ in ids)
        async with self._db.transaction() as tx:
            await tx.execute(
                f"DELETE FROM directory_pairs WHERE tenant_id = ? AND id IN ({marks})",
                tenant_id,
                *ids,
            )

    async def of_owner(self, tenant_id: str, user_id: str | None) -> list[Pair]:
        await self._ensure()
        async with self._db.transaction() as tx:
            if user_id is None:
                rows = await tx.fetchall(
                    f"SELECT {_COLUMNS} FROM directory_pairs WHERE tenant_id = ?", tenant_id
                )
            else:
                rows = await tx.fetchall(
                    f"SELECT {_COLUMNS} FROM directory_pairs WHERE tenant_id = ? AND user_id = ?",
                    tenant_id,
                    user_id,
                )
        return [_pair(r) for r in rows]
