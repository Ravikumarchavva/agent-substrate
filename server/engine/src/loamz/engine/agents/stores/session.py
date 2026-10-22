"""Session state: the small key/value state one conversation keeps across runs (a ``ShortTermMemory``).

A session id is the conversation's (thread's) id as the engine sees it; a tenant's view of the store places it under
the tenant (``stores/scoped.py``), so ``erase_under(tenant)`` removes every session the tenant had.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from loamz.engine.sql import Tx, under

if TYPE_CHECKING:
    from loamz.engine.agents.stores.store import AgentStore

COMPONENT = "session"
SCRIPTS = [
    """
CREATE TABLE IF NOT EXISTS session_state (
    session_id TEXT PRIMARY KEY,
    state_json TEXT NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL
);
"""
]


class SessionState:
    """The ``ShortTermMemory`` of an ``AgentStore``: ``store.session_state``."""

    def __init__(self, store: AgentStore) -> None:
        self._store = store

    async def _run[T](self, fn: Callable[[Tx], Awaitable[T]]) -> T:
        await self._store.ensure(COMPONENT, SCRIPTS)
        return await self._store.run(fn)

    async def get_state(self, session_id: str) -> dict[str, Any]:
        async def op(tx: Tx) -> dict[str, Any]:
            row = await tx.fetchone(
                "SELECT state_json FROM session_state WHERE session_id = ?", session_id
            )
            return json.loads(row["state_json"]) if row else {}

        return await self._run(op)

    async def set_state(self, session_id: str, state: dict[str, Any]) -> None:
        async def op(tx: Tx) -> None:
            await _write(tx, session_id, state)

        await self._run(op)

    async def update_state(self, session_id: str, patch: dict[str, Any]) -> None:
        async def op(tx: Tx) -> None:
            await tx.lock(f"session_state:{session_id}")
            row = await tx.fetchone(
                "SELECT state_json FROM session_state WHERE session_id = ?", session_id
            )
            state = json.loads(row["state_json"]) if row else {}
            state.update(patch)
            await _write(tx, session_id, state)

        await self._run(op)

    async def clear(self, session_id: str) -> None:
        async def op(tx: Tx) -> None:
            await tx.execute("DELETE FROM session_state WHERE session_id = ?", session_id)

        await self._run(op)

    async def erase_under(self, name: str) -> int:
        """Remove the state of every session at or below ``name`` (a tenant's prefix)."""

        async def op(tx: Tx) -> int:
            where, params = under("session_id", name)
            return await tx.execute(f"DELETE FROM session_state WHERE {where}", *params)

        return await self._run(op)


async def _write(tx: Tx, session_id: str, state: dict[str, Any]) -> None:
    await tx.execute(
        "INSERT INTO session_state (session_id, state_json, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT (session_id) DO UPDATE SET state_json = excluded.state_json, "
        "updated_at = excluded.updated_at",
        session_id,
        json.dumps(state, default=str),
        time.time(),
    )


__all__ = ["COMPONENT", "SCRIPTS", "SessionState"]
