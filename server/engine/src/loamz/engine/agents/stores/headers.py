"""Thread headers: what a conversation is called and who owns it, beside its history.

The history of a thread (its message DAG, branches and checkpoints) is the engine's (``stores/threads.py``); the
header is what a person organises it by: a name, tags, free-form metadata, the agent it is with, and whether it is
pinned, archived, locked or deleted. Every statement is keyed by ``tenant_id``; reads that a person makes are keyed by
``(tenant_id, user_id)`` too, so another person's thread looks exactly like one that does not exist.

Lists are newest-activity first with pinned threads ahead (``pin_rank`` is 0, or the time it was pinned), paged by
an opaque cursor over ``(pin_rank, updated_at, id)``: no offsets, and stable while threads are added.

A delete is soft (``deleted_at``): the owner no longer sees the thread, the row stays for review; ``erase`` is the
hard removal for a person's or a tenant's erasure.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import Field

from loamz.engine.agents.kernel.types.content import JsonObject, KernelModel
from loamz.engine.agents.kernel.types.ids import new_id
from loamz.engine.errors import InvalidRequestError
from loamz.engine.sql import Row, Tx

if TYPE_CHECKING:
    from loamz.engine.agents.stores.store import AgentStore

COMPONENT = "thread_headers"
SCRIPTS = [
    """
CREATE TABLE IF NOT EXISTS thread_headers (
    tenant_id TEXT NOT NULL,
    id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    name TEXT,
    metadata_json TEXT NOT NULL,
    agent_id TEXT,
    pin_rank DOUBLE PRECISION NOT NULL,
    archived_at DOUBLE PRECISION,
    locked_at DOUBLE PRECISION,
    locked_reason TEXT,
    deleted_at DOUBLE PRECISION,
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS thread_headers_owner_idx
    ON thread_headers (tenant_id, user_id, pin_rank, updated_at, id);
CREATE TABLE IF NOT EXISTS thread_header_tags (
    tenant_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY (tenant_id, thread_id, tag)
);
"""
]

_COLUMNS = (
    "tenant_id, id, user_id, name, metadata_json, agent_id, pin_rank, archived_at, locked_at, "
    "locked_reason, deleted_at, created_at, updated_at"
)
_UNSET: Any = object()


def _ts(value: float | None) -> datetime | None:
    return None if value is None else datetime.fromtimestamp(value, UTC)


class ThreadHeader(KernelModel):
    """A conversation as its owner organises it."""

    id: str = Field(default_factory=new_id)
    tenant_id: str
    user_id: str
    name: str | None = None
    tags: tuple[str, ...] = ()
    metadata: JsonObject = Field(default_factory=dict)
    agent_id: str | None = None
    pinned_at: datetime | None = None
    archived_at: datetime | None = None
    locked_at: datetime | None = None
    locked_reason: str | None = None
    deleted_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ThreadPage(KernelModel):
    items: tuple[ThreadHeader, ...]
    next_page_token: str | None = None


def _header(row: Row, tags: Sequence[str]) -> ThreadHeader:
    return ThreadHeader(
        id=row["id"],
        tenant_id=row["tenant_id"],
        user_id=row["user_id"],
        name=row["name"],
        tags=tuple(tags),
        metadata=json.loads(row["metadata_json"]),
        agent_id=row["agent_id"],
        pinned_at=_ts(row["pin_rank"] or None),
        archived_at=_ts(row["archived_at"]),
        locked_at=_ts(row["locked_at"]),
        locked_reason=row["locked_reason"],
        deleted_at=_ts(row["deleted_at"]),
        created_at=_ts(row["created_at"]),
        updated_at=_ts(row["updated_at"]),
    )


def _encode_cursor(row: Row) -> str:
    raw = json.dumps([row["pin_rank"], row["updated_at"], row["id"]])
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(token: str) -> tuple[float, float, str]:
    try:
        padded = token + "=" * (-len(token) % 4)
        pin_rank, updated_at, thread_id = json.loads(base64.urlsafe_b64decode(padded))
        return float(pin_rank), float(updated_at), str(thread_id)
    except (ValueError, TypeError) as exc:
        raise InvalidRequestError("page_token is not one this server issued") from exc


async def _tags_of(tx: Tx, tenant_id: str, ids: Sequence[str]) -> dict[str, list[str]]:
    if not ids:
        return {}
    marks = ", ".join("?" for _ in ids)
    rows = await tx.fetchall(
        f"SELECT thread_id, tag FROM thread_header_tags WHERE tenant_id = ? AND thread_id IN ({marks}) "
        "ORDER BY tag",
        tenant_id,
        *ids,
    )
    out: dict[str, list[str]] = {}
    for row in rows:
        out.setdefault(row["thread_id"], []).append(row["tag"])
    return out


async def _set_tags(tx: Tx, tenant_id: str, thread_id: str, tags: Sequence[str]) -> None:
    await tx.execute(
        "DELETE FROM thread_header_tags WHERE tenant_id = ? AND thread_id = ?", tenant_id, thread_id
    )
    for tag in dict.fromkeys(tags):
        await tx.execute(
            "INSERT INTO thread_header_tags (tenant_id, thread_id, tag) VALUES (?, ?, ?)",
            tenant_id,
            thread_id,
            tag,
        )


class ThreadHeaders:
    """The headers of an ``AgentStore``: ``store.thread_headers``."""

    def __init__(self, store: AgentStore) -> None:
        self._store = store

    async def _run[T](self, fn: Callable[[Tx], Awaitable[T]]) -> T:
        await self._store.ensure(COMPONENT, SCRIPTS)
        return await self._store.run(fn)

    async def _read(self, tx: Tx, tenant_id: str, thread_id: str) -> ThreadHeader | None:
        row = await tx.fetchone(
            f"SELECT {_COLUMNS} FROM thread_headers WHERE tenant_id = ? AND id = ?",
            tenant_id,
            thread_id,
        )
        if row is None:
            return None
        tags = await _tags_of(tx, tenant_id, [thread_id])
        return _header(row, tags.get(thread_id, []))

    async def create(self, header: ThreadHeader) -> ThreadHeader:
        """Record a new thread. Creating one that exists returns the existing header unchanged (idempotent)."""

        async def op(tx: Tx) -> ThreadHeader:
            await tx.execute(
                f"INSERT INTO thread_headers ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT DO NOTHING",
                header.tenant_id,
                header.id,
                header.user_id,
                header.name,
                json.dumps(header.metadata, default=str),
                header.agent_id,
                header.pinned_at.timestamp() if header.pinned_at else 0.0,
                header.archived_at.timestamp() if header.archived_at else None,
                header.locked_at.timestamp() if header.locked_at else None,
                header.locked_reason,
                None,
                header.created_at.timestamp(),
                header.updated_at.timestamp(),
            )
            existing = await self._read(tx, header.tenant_id, header.id)
            assert existing is not None
            if existing.user_id == header.user_id and not existing.tags and header.tags:
                await _set_tags(tx, header.tenant_id, header.id, header.tags)
                existing = existing.model_copy(update={"tags": tuple(dict.fromkeys(header.tags))})
            return existing

        return await self._run(op)

    async def get(
        self, tenant_id: str, thread_id: str, *, include_deleted: bool = False
    ) -> ThreadHeader | None:
        """A thread of the tenant, whoever owns it (for the server's own checks; people use ``get_owned``)."""

        async def op(tx: Tx) -> ThreadHeader | None:
            header = await self._read(tx, tenant_id, thread_id)
            if header is None or (header.deleted_at is not None and not include_deleted):
                return None
            return header

        return await self._run(op)

    async def get_owned(self, tenant_id: str, user_id: str, thread_id: str) -> ThreadHeader | None:
        """The caller's own live thread, or ``None`` (absent, deleted and someone else's look the same)."""
        header = await self.get(tenant_id, thread_id)
        return header if header is not None and header.user_id == user_id else None

    async def list(
        self,
        tenant_id: str,
        user_id: str,
        *,
        archived: bool = False,
        agent_id: str | None = _UNSET,
        without_tag: str | None = None,
        page_size: int = 50,
        page_token: str | None = None,
    ) -> ThreadPage:
        """The person's live threads: pinned first, then by latest activity.

        ``archived`` picks the archive instead of the main list; ``agent_id`` keeps only the threads with that agent
        (``None``: only those with no agent; omitted: any); ``without_tag`` leaves out threads carrying that tag."""
        page_size = max(1, min(page_size, 200))

        async def op(tx: Tx) -> ThreadPage:
            where = [
                "h.tenant_id = ?",
                "h.user_id = ?",
                "h.deleted_at IS NULL",
                "h.archived_at IS NOT NULL" if archived else "h.archived_at IS NULL",
            ]
            params: list[Any] = [tenant_id, user_id]
            if agent_id is None:
                where.append("h.agent_id IS NULL")
            elif agent_id is not _UNSET:
                where.append("h.agent_id = ?")
                params.append(agent_id)
            if without_tag is not None:
                where.append(
                    "NOT EXISTS (SELECT 1 FROM thread_header_tags t WHERE t.tenant_id = h.tenant_id "
                    "AND t.thread_id = h.id AND t.tag = ?)"
                )
                params.append(without_tag)
            if page_token:
                pin_rank, updated_at, last_id = _decode_cursor(page_token)
                where.append("(h.pin_rank, h.updated_at, h.id) < (?, ?, ?)")
                params += [pin_rank, updated_at, last_id]
            columns = ", ".join(f"h.{c.strip()}" for c in _COLUMNS.split(","))
            rows = await tx.fetchall(
                f"SELECT {columns} FROM thread_headers h WHERE {' AND '.join(where)} "
                "ORDER BY h.pin_rank DESC, h.updated_at DESC, h.id DESC LIMIT ?",
                *params,
                page_size + 1,
            )
            more = len(rows) > page_size
            rows = rows[:page_size]
            tags = await _tags_of(tx, tenant_id, [r["id"] for r in rows])
            return ThreadPage(
                items=tuple(_header(r, tags.get(r["id"], [])) for r in rows),
                next_page_token=_encode_cursor(rows[-1]) if more else None,
            )

        return await self._run(op)

    async def owned_ids(
        self,
        tenant_id: str,
        user_id: str,
        *,
        limit: int | None = 500,
        include_deleted: bool = False,
    ) -> list[str]:
        """Ids of the person's threads (archived ones included; deleted ones only if asked), latest activity first."""

        async def op(tx: Tx) -> list[str]:
            live = "" if include_deleted else " AND deleted_at IS NULL"
            rows = await tx.fetchall(
                f"SELECT id FROM thread_headers WHERE tenant_id = ? AND user_id = ?{live} "
                "ORDER BY updated_at DESC, id DESC" + ("" if limit is None else " LIMIT ?"),
                tenant_id,
                user_id,
                *(() if limit is None else (limit,)),
            )
            return [r["id"] for r in rows]

        return await self._run(op)

    async def update(
        self,
        tenant_id: str,
        thread_id: str,
        *,
        name: str | None = _UNSET,
        tags: Sequence[str] | None = None,
        metadata: JsonObject | None = None,
        agent_id: str | None = _UNSET,
        pinned: bool | None = None,
        archived: bool | None = None,
    ) -> ThreadHeader | None:
        """Change what is given. Pinning and archiving organise the list and do not count as activity (they leave
        ``updated_at``); archiving unpins. ``None`` if there is no such live thread."""

        async def op(tx: Tx) -> ThreadHeader | None:
            current = await self._read(tx, tenant_id, thread_id)
            if current is None or current.deleted_at is not None:
                return None
            now = time.time()
            sets: list[str] = []
            params: list[Any] = []
            activity = False
            if name is not _UNSET:
                sets.append("name = ?")
                params.append(name)
                activity = True
            if metadata is not None:
                sets.append("metadata_json = ?")
                params.append(json.dumps(metadata, default=str))
                activity = True
            if agent_id is not _UNSET:
                sets.append("agent_id = ?")
                params.append(agent_id)
                activity = True
            if pinned is not None:
                sets.append("pin_rank = ?")
                params.append(now if pinned else 0.0)
            if archived is not None:
                sets.append("archived_at = ?")
                params.append(now if archived else None)
                if archived:
                    sets.append("pin_rank = 0")
            if tags is not None:
                await _set_tags(tx, tenant_id, thread_id, tags)
                activity = True
            if activity:
                sets.append("updated_at = ?")
                params.append(now)
            if sets:
                await tx.execute(
                    f"UPDATE thread_headers SET {', '.join(sets)} WHERE tenant_id = ? AND id = ?",
                    *params,
                    tenant_id,
                    thread_id,
                )
            return await self._read(tx, tenant_id, thread_id)

        return await self._run(op)

    async def touch(self, tenant_id: str, thread_id: str) -> None:
        """Record activity (a new message): the thread moves to the top of its owner's list."""

        async def op(tx: Tx) -> None:
            await tx.execute(
                "UPDATE thread_headers SET updated_at = ? WHERE tenant_id = ? AND id = ?",
                time.time(),
                tenant_id,
                thread_id,
            )

        await self._run(op)

    async def lock(self, tenant_id: str, thread_id: str, reason: str) -> None:
        """Stop further runs on a thread (a file it depends on is gone). Locking a locked thread keeps its reason."""

        async def op(tx: Tx) -> None:
            await tx.execute(
                "UPDATE thread_headers SET locked_at = ?, locked_reason = ? "
                "WHERE tenant_id = ? AND id = ? AND locked_at IS NULL",
                time.time(),
                reason,
                tenant_id,
                thread_id,
            )

        await self._run(op)

    async def unlock(self, tenant_id: str, thread_id: str) -> None:
        async def op(tx: Tx) -> None:
            await tx.execute(
                "UPDATE thread_headers SET locked_at = NULL, locked_reason = NULL WHERE tenant_id = ? AND id = ?",
                tenant_id,
                thread_id,
            )

        await self._run(op)

    async def delete(self, tenant_id: str, thread_id: str) -> bool:
        """Soft-delete: hidden from its owner, kept for review. ``False`` if there was no live thread."""

        async def op(tx: Tx) -> bool:
            now = time.time()
            changed = await tx.execute(
                "UPDATE thread_headers SET deleted_at = ?, updated_at = ? "
                "WHERE tenant_id = ? AND id = ? AND deleted_at IS NULL",
                now,
                now,
                tenant_id,
                thread_id,
            )
            return changed > 0

        return await self._run(op)

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        """Remove a person's headers, or (no user) the whole tenant's, for good. Returns how many."""

        async def op(tx: Tx) -> int:
            if user_id is None:
                await tx.execute("DELETE FROM thread_header_tags WHERE tenant_id = ?", tenant_id)
                return await tx.execute("DELETE FROM thread_headers WHERE tenant_id = ?", tenant_id)
            await tx.execute(
                "DELETE FROM thread_header_tags WHERE tenant_id = ? AND thread_id IN "
                "(SELECT id FROM thread_headers WHERE tenant_id = ? AND user_id = ?)",
                tenant_id,
                tenant_id,
                user_id,
            )
            return await tx.execute(
                "DELETE FROM thread_headers WHERE tenant_id = ? AND user_id = ?", tenant_id, user_id
            )

        return await self._run(op)


__all__ = ["COMPONENT", "SCRIPTS", "ThreadHeader", "ThreadHeaders", "ThreadPage"]
