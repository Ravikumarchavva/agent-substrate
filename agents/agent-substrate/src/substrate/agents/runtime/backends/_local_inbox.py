"""LocalInbox — SQLite-durable InboxProtocol (no-infra tier).

Satisfies the same robustness guarantees as InMemoryInbox: dedup by
Message.id, per-sender FIFO delivery order, retry + dead-letter after
max_retries nacks — just durable across restarts.
"""

from __future__ import annotations

import sqlite3
from typing import Callable

from substrate.kernel.core.identity import Actor
from substrate.kernel.messaging.message import Message
from substrate.kernel.runtime.inbox import DeadLetterEntry, DeadLetterReason

from ._local_db import LocalRuntimeDB


class LocalInbox:
    """SQLite-backed InboxProtocol."""

    def __init__(self, db: LocalRuntimeDB, *, max_retries: int = 3) -> None:
        self._db = db
        self._max_retries = max_retries
        self._on_deliver: Callable[[Actor], None] | None = None

    def set_deliver_hook(self, cb: Callable[[Actor], None] | None) -> None:
        self._on_deliver = cb

    @staticmethod
    def _sender_key(msg: Message) -> str:
        return str(msg.sender)

    async def deliver(
        self, agent_id: Actor, msg: Message, *, notify: bool = True
    ) -> bool:
        agent_str = str(agent_id)

        def _do(conn: sqlite3.Connection) -> bool:
            existing = conn.execute(
                "SELECT 1 FROM inbox_messages WHERE agent_id = ? AND msg_id = ?",
                (agent_str, msg.id),
            ).fetchone()
            if existing is not None:
                return False
            row = conn.execute(
                "SELECT COALESCE(MAX(ord), -1) AS m FROM inbox_messages WHERE agent_id = ?",
                (agent_str,),
            ).fetchone()
            next_ord = row["m"] + 1
            conn.execute(
                "INSERT INTO inbox_messages (agent_id, msg_id, sender_key, msg_json, ord) "
                "VALUES (?, ?, ?, ?, ?)",
                (agent_str, msg.id, self._sender_key(msg), msg.model_dump_json(), next_ord),
            )
            return True

        delivered = await self._db.run(_do)
        if delivered and notify and self._on_deliver:
            self._on_deliver(agent_id)
        return delivered

    async def drain(self, agent_id: Actor, *, max: int = 100) -> list[Message]:
        agent_str = str(agent_id)

        def _do(conn: sqlite3.Connection) -> list[Message]:
            rows = conn.execute(
                "SELECT msg_json FROM inbox_messages WHERE agent_id = ? "
                "ORDER BY sender_key, ord LIMIT ?",
                (agent_str, max),
            ).fetchall()
            return [Message.model_validate_json(r["msg_json"]) for r in rows]

        return await self._db.run(_do)

    async def ack(self, agent_id: Actor, msg_id: str) -> None:
        agent_str = str(agent_id)

        def _do(conn: sqlite3.Connection) -> None:
            conn.execute(
                "DELETE FROM inbox_messages WHERE agent_id = ? AND msg_id = ?",
                (agent_str, msg_id),
            )
            conn.execute(
                "DELETE FROM inbox_retries WHERE agent_id = ? AND msg_id = ?",
                (agent_str, msg_id),
            )

        await self._db.run(_do)

    async def nack(self, agent_id: Actor, msg_id: str, *, error: str = "") -> None:
        agent_str = str(agent_id)

        def _do(conn: sqlite3.Connection) -> tuple[int, str | None]:
            row = conn.execute(
                "SELECT attempts FROM inbox_retries WHERE agent_id = ? AND msg_id = ?",
                (agent_str, msg_id),
            ).fetchone()
            attempts = (row["attempts"] if row else 0) + 1
            conn.execute(
                "INSERT INTO inbox_retries (agent_id, msg_id, attempts) VALUES (?, ?, ?) "
                "ON CONFLICT (agent_id, msg_id) DO UPDATE SET attempts = excluded.attempts",
                (agent_str, msg_id, attempts),
            )
            msg_row = conn.execute(
                "SELECT msg_json FROM inbox_messages WHERE agent_id = ? AND msg_id = ?",
                (agent_str, msg_id),
            ).fetchone()
            return attempts, msg_row["msg_json"] if msg_row else None

        attempts, msg_json = await self._db.run(_do)
        if attempts >= self._max_retries and msg_json is not None:
            msg = Message.model_validate_json(msg_json)
            entry = DeadLetterEntry(
                agent_id=agent_id,
                msg=msg,
                reason=DeadLetterReason.MAX_RETRIES,
                attempts=attempts,
                last_error=error or None,
            )

            def _dead(conn: sqlite3.Connection) -> None:
                row = conn.execute(
                    "SELECT COALESCE(MAX(ord), -1) AS m FROM inbox_dead_letters WHERE agent_id = ?",
                    (agent_str,),
                ).fetchone()
                conn.execute(
                    "INSERT INTO inbox_dead_letters (agent_id, msg_id, entry_json, ord) "
                    "VALUES (?, ?, ?, ?)",
                    (agent_str, msg_id, entry.model_dump_json(), row["m"] + 1),
                )

            await self._db.run(_dead)
            await self.ack(agent_id, msg_id)

    async def dead_letters(self, agent_id: Actor) -> list[DeadLetterEntry]:
        agent_str = str(agent_id)

        def _do(conn: sqlite3.Connection) -> list[DeadLetterEntry]:
            rows = conn.execute(
                "SELECT entry_json FROM inbox_dead_letters WHERE agent_id = ? ORDER BY ord",
                (agent_str,),
            ).fetchall()
            return [DeadLetterEntry.model_validate_json(r["entry_json"]) for r in rows]

        return await self._db.run(_do)

    async def pending_count(self, agent_id: Actor) -> int:
        agent_str = str(agent_id)

        def _do(conn: sqlite3.Connection) -> int:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM inbox_messages WHERE agent_id = ?",
                (agent_str,),
            ).fetchone()
            return row["c"]

        return await self._db.run(_do)


__all__ = ["LocalInbox"]
