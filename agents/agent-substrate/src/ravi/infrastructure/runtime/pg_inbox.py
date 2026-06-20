"""PostgresInbox — Stage 1 durable Inbox backed by asyncpg.

Schema::

    CREATE TABLE ravi_inbox (
        agent_id   TEXT        NOT NULL,
        msg_id     TEXT        NOT NULL,
        sender_key TEXT        NOT NULL DEFAULT '__anon__',
        payload    JSONB       NOT NULL,
        attempts   INTEGER     NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (agent_id, msg_id)
    );

    CREATE TABLE ravi_dead_letters (
        agent_id   TEXT        NOT NULL,
        msg_id     TEXT        NOT NULL,
        reason     TEXT        NOT NULL,
        attempts   INTEGER     NOT NULL,
        last_error TEXT,
        payload    JSONB       NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (agent_id, msg_id)
    );

Delivery guarantees
-------------------
- ``deliver`` is idempotent via ON CONFLICT DO NOTHING (dedup by msg_id).
- ``drain`` returns messages in per-sender FIFO order (sender_key, created_at).
- ``nack`` increments the attempt counter; after ``max_retries`` the message
  is moved to ravi_dead_letters.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Callable

from ravi.kernel.core.identity import AgentId
from ravi.kernel.messaging.message import Message
from ravi.kernel.runtime.inbox import DeadLetterEntry, DeadLetterReason

if TYPE_CHECKING:
    import asyncpg

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS ravi_inbox (
    agent_id   TEXT        NOT NULL,
    msg_id     TEXT        NOT NULL,
    sender_key TEXT        NOT NULL DEFAULT '__anon__',
    payload    JSONB       NOT NULL,
    attempts   INTEGER     NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_id, msg_id)
);

CREATE TABLE IF NOT EXISTS ravi_dead_letters (
    agent_id   TEXT        NOT NULL,
    msg_id     TEXT        NOT NULL,
    reason     TEXT        NOT NULL,
    attempts   INTEGER     NOT NULL,
    last_error TEXT,
    payload    JSONB       NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_id, msg_id)
);
"""


class PostgresInbox:
    """Postgres-backed Inbox implementing the kernel Inbox Protocol."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        max_retries: int = 3,
    ) -> None:
        self._pool = pool
        self._max_retries = max_retries
        self._on_deliver: Callable[[AgentId], None] | None = None

    def set_deliver_hook(self, cb: Callable[[AgentId], None] | None) -> None:
        """Wire the Runtime's wakeup callback, invoked after each new delivery."""
        self._on_deliver = cb

    async def setup(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(_CREATE_TABLES)

    async def deliver(
        self, agent_id: AgentId, msg: Message, *, notify: bool = True
    ) -> bool:
        sender_key = str(msg.sender) if msg.sender else "__anon__"
        payload_json = msg.model_dump_json()
        async with self._pool.acquire() as conn:
            result = await conn.fetchrow(
                """
                INSERT INTO ravi_inbox (agent_id, msg_id, sender_key, payload)
                VALUES ($1, $2, $3, $4::jsonb)
                ON CONFLICT (agent_id, msg_id) DO NOTHING
                RETURNING msg_id
                """,
                str(agent_id),
                msg.id,
                sender_key,
                payload_json,
            )
        if result is None:
            return False  # duplicate
        if notify and self._on_deliver:
            self._on_deliver(agent_id)
        return True

    async def drain(self, agent_id: AgentId, *, max: int = 100) -> list[Message]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT payload
                FROM ravi_inbox
                WHERE agent_id = $1
                ORDER BY sender_key, created_at
                LIMIT $2
                """,
                str(agent_id),
                max,
            )
        return [Message.model_validate_json(row["payload"]) for row in rows]

    async def ack(self, agent_id: AgentId, msg_id: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM ravi_inbox WHERE agent_id = $1 AND msg_id = $2",
                str(agent_id),
                msg_id,
            )

    async def nack(
        self,
        agent_id: AgentId,
        msg_id: str,
        *,
        error: str = "",
    ) -> None:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    UPDATE ravi_inbox
                    SET attempts = attempts + 1
                    WHERE agent_id = $1 AND msg_id = $2
                    RETURNING attempts, payload
                    """,
                    str(agent_id),
                    msg_id,
                )
                if row is None:
                    return
                attempts: int = row["attempts"]
                if attempts >= self._max_retries:
                    await conn.execute(
                        """
                        INSERT INTO ravi_dead_letters
                            (agent_id, msg_id, reason, attempts, last_error, payload)
                        VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                        ON CONFLICT (agent_id, msg_id) DO UPDATE
                            SET attempts = EXCLUDED.attempts,
                                last_error = EXCLUDED.last_error
                        """,
                        str(agent_id),
                        msg_id,
                        DeadLetterReason.MAX_RETRIES.value,
                        attempts,
                        error or None,
                        row["payload"],
                    )
                    await conn.execute(
                        "DELETE FROM ravi_inbox WHERE agent_id = $1 AND msg_id = $2",
                        str(agent_id),
                        msg_id,
                    )

    async def dead_letters(self, agent_id: AgentId) -> list[DeadLetterEntry]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT msg_id, reason, attempts, last_error, payload
                FROM ravi_dead_letters
                WHERE agent_id = $1
                ORDER BY created_at
                """,
                str(agent_id),
            )
        entries: list[DeadLetterEntry] = []
        for row in rows:
            raw = row["payload"]
            payload_str = raw if isinstance(raw, str) else json.dumps(raw)
            msg = Message.model_validate_json(payload_str)
            entries.append(
                DeadLetterEntry(
                    agent_id=agent_id,
                    msg=msg,
                    reason=DeadLetterReason(row["reason"]),
                    attempts=row["attempts"],
                    last_error=row["last_error"],
                )
            )
        return entries

    async def pending_count(self, agent_id: AgentId) -> int:
        async with self._pool.acquire() as conn:
            return (
                await conn.fetchval(
                    "SELECT COUNT(*) FROM ravi_inbox WHERE agent_id = $1",
                    str(agent_id),
                )
                or 0
            )


__all__ = ["PostgresInbox"]
