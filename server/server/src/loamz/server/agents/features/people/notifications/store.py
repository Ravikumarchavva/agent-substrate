from __future__ import annotations

import base64
import json
import time
from datetime import UTC, datetime

from loamz.engine.agents.kernel.types.ids import new_id
from loamz.engine.errors import InvalidRequestError
from loamz.engine.sql import Database, Row, migrate
from loamz.server.agents.features.people.notifications.model import Notification

COMPONENT = "people.notifications"
SCRIPTS = [
    """
    CREATE TABLE IF NOT EXISTS people_notifications (
        tenant_id TEXT NOT NULL,
        id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        thread_id TEXT,
        link TEXT,
        created_at DOUBLE PRECISION NOT NULL,
        read_at DOUBLE PRECISION,
        PRIMARY KEY (tenant_id, id)
    );
    CREATE INDEX IF NOT EXISTS people_notifications_owner_idx
        ON people_notifications (tenant_id, user_id, created_at, id)
    """,
]

_COLUMNS = "id, kind, title, body, thread_id, link, created_at, read_at"


def _ts(value: float | None) -> datetime | None:
    return None if value is None else datetime.fromtimestamp(value, UTC)


def _notification(row: Row) -> Notification:
    return Notification(
        id=row["id"],
        kind=row["kind"],
        title=row["title"],
        body=row["body"],
        thread_id=row["thread_id"],
        link=row["link"],
        created_at=_ts(row["created_at"]),  # type: ignore[arg-type]
        read_at=_ts(row["read_at"]),
    )


def _encode(row: Row) -> str:
    raw = json.dumps([row["created_at"], row["id"]])
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode(token: str) -> tuple[float, str]:
    try:
        created_at, last_id = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
        return float(created_at), str(last_id)
    except (ValueError, TypeError) as exc:
        raise InvalidRequestError("page_token is not one this server issued") from exc


class NotificationStore:
    """Every statement is keyed by tenant and user: nobody reads, or marks read, another person's notices."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self._ready = False

    async def _ensure(self) -> None:
        if not self._ready:
            await migrate(self._db, COMPONENT, SCRIPTS)
            self._ready = True

    async def add(
        self,
        tenant_id: str,
        user_id: str,
        *,
        kind: str,
        title: str,
        body: str,
        thread_id: str | None,
        link: str | None,
    ) -> Notification:
        await self._ensure()
        notification_id, now = new_id(), time.time()
        async with self._db.transaction() as tx:
            await tx.execute(
                "INSERT INTO people_notifications "
                "(tenant_id, id, user_id, kind, title, body, thread_id, link, created_at, read_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                tenant_id,
                notification_id,
                user_id,
                kind,
                title,
                body,
                thread_id,
                link,
                now,
            )
        return Notification(
            id=notification_id,
            kind=kind,
            title=title,
            body=body,
            thread_id=thread_id,
            link=link,
            created_at=_ts(now),  # type: ignore[arg-type]
        )

    async def list(
        self, tenant_id: str, user_id: str, *, page_size: int, page_token: str | None
    ) -> tuple[list[Notification], str | None, int]:
        """Newest first: `(page, next token, unread count)`."""
        await self._ensure()
        where, params = "tenant_id = ? AND user_id = ?", [tenant_id, user_id]
        if page_token:
            created_at, last_id = _decode(page_token)
            where += " AND (created_at, id) < (?, ?)"
            params += [created_at, last_id]
        async with self._db.transaction() as tx:
            rows = await tx.fetchall(
                f"SELECT {_COLUMNS} FROM people_notifications WHERE {where} "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                *params,
                page_size + 1,
            )
            unread = await tx.fetchone(
                "SELECT COUNT(*) AS n FROM people_notifications "
                "WHERE tenant_id = ? AND user_id = ? AND read_at IS NULL",
                tenant_id,
                user_id,
            )
        more = len(rows) > page_size
        rows = rows[:page_size]
        return (
            [_notification(r) for r in rows],
            _encode(rows[-1]) if more else None,
            int(unread["n"]) if unread else 0,
        )

    async def mark_read(self, tenant_id: str, user_id: str, notification_id: str) -> bool:
        await self._ensure()
        async with self._db.transaction() as tx:
            changed = await tx.execute(
                "UPDATE people_notifications SET read_at = COALESCE(read_at, ?) "
                "WHERE tenant_id = ? AND user_id = ? AND id = ?",
                time.time(),
                tenant_id,
                user_id,
                notification_id,
            )
        return changed > 0

    async def mark_all_read(self, tenant_id: str, user_id: str) -> int:
        await self._ensure()
        async with self._db.transaction() as tx:
            return await tx.execute(
                "UPDATE people_notifications SET read_at = ? "
                "WHERE tenant_id = ? AND user_id = ? AND read_at IS NULL",
                time.time(),
                tenant_id,
                user_id,
            )

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        await self._ensure()
        async with self._db.transaction() as tx:
            if user_id is None:
                return await tx.execute(
                    "DELETE FROM people_notifications WHERE tenant_id = ?", tenant_id
                )
            return await tx.execute(
                "DELETE FROM people_notifications WHERE tenant_id = ? AND user_id = ?",
                tenant_id,
                user_id,
            )
