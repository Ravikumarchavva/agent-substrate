"""Cross-tenant, read-mostly queries for platform administration.

These read the agent store's own tables (`thread_headers`, the run journal) without a tenant key on purpose: that is
what an administrator's overview is. The service decides who may see which tenant and passes `tenant_id` to narrow.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

from loamz.agents.stores import AgentStore
from loamz.data.storage.errors import InvalidRequestError
from loamz.data.storage.sql import Tx

HEADERS = "thread_headers"
RUNTIME = "runtime"


def _iso(value: float | None) -> str | None:
    return None if value is None else datetime.fromtimestamp(value, UTC).isoformat()


def _token(*parts: Any) -> str:
    return base64.urlsafe_b64encode(json.dumps(list(parts)).encode()).decode().rstrip("=")


def _untoken(token: str, size: int) -> list[Any]:
    try:
        parts = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
        if len(parts) != size:
            raise ValueError
        return parts
    except (ValueError, TypeError) as exc:
        raise InvalidRequestError("page_token is not one this server issued") from exc


class AdminStore:
    def __init__(self, store: AgentStore) -> None:
        self._store = store

    async def stats(self, tenant_id: str | None) -> tuple[int, int]:
        """`(threads, journal events)` of one tenant, or of all of them."""
        scope, args = ("WHERE tenant_id = ?", [tenant_id]) if tenant_id else ("", [])
        run_scope, run_args = ("WHERE r.tenant = ?", [tenant_id]) if tenant_id else ("", [])

        async def read(tx: Tx) -> tuple[int, int]:
            threads = events = 0
            if await self._store.has(HEADERS):
                row = await tx.fetchone(f"SELECT COUNT(*) AS n FROM thread_headers {scope}", *args)
                threads = int(row["n"])
            if await self._store.has(RUNTIME):
                row = await tx.fetchone(
                    "SELECT COUNT(*) AS n FROM rt_events e JOIN rt_runs r ON r.run_id = e.run_id "
                    f"{run_scope}",
                    *run_args,
                )
                events = int(row["n"])
            return threads, events

        return await self._store.run(read)

    async def threads(
        self, tenant_id: str | None, page_size: int, page_token: str | None
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Threads (deleted ones included), newest activity first, with their journal event counts."""
        if not await self._store.has(HEADERS):
            return [], None
        has_runtime = await self._store.has(RUNTIME)
        where, args = ["1 = 1"], []
        if tenant_id:
            where.append("h.tenant_id = ?")
            args.append(tenant_id)
        if page_token:
            updated_at, last_tenant, last_id = _untoken(page_token, 3)
            where.append("(h.updated_at, h.tenant_id, h.id) < (?, ?, ?)")
            args += [float(updated_at), str(last_tenant), str(last_id)]
        count = (
            "(SELECT COUNT(*) FROM rt_events e JOIN rt_runs r ON r.run_id = e.run_id "
            "WHERE r.thread_id = h.id AND r.tenant = h.tenant_id)"
            if has_runtime
            else "0"
        )

        async def read(tx: Tx) -> list:
            return list(
                await tx.fetchall(
                    "SELECT h.tenant_id, h.id, h.name, h.user_id, h.created_at, h.updated_at, "
                    f"h.deleted_at, {count} AS event_count FROM thread_headers h "  # noqa: S608 - fixed fragments
                    f"WHERE {' AND '.join(where)} "
                    "ORDER BY h.updated_at DESC, h.tenant_id DESC, h.id DESC LIMIT ?",
                    *args,
                    page_size + 1,
                )
            )

        rows = await self._store.run(read)
        more = len(rows) > page_size
        rows = rows[:page_size]
        items = [
            {
                "id": r["id"],
                "tenant_id": r["tenant_id"],
                "name": r["name"] or "Untitled",
                "user_identifier": r["user_id"],
                "created_at": _iso(r["created_at"]),
                "updated_at": _iso(r["updated_at"]),
                "deleted_at": _iso(r["deleted_at"]),
                "event_count": int(r["event_count"]),
            }
            for r in rows
        ]
        last = rows[-1] if rows else None
        return items, (_token(last["updated_at"], last["tenant_id"], last["id"]) if more else None)

    async def find_threads(self, tenant_id: str | None, thread_id: str) -> list[str]:
        """The tenants that have a thread with this id (usually one), narrowed to `tenant_id` when given."""
        if not await self._store.has(HEADERS):
            return []

        async def read(tx: Tx) -> list[str]:
            sql, args = "SELECT tenant_id FROM thread_headers WHERE id = ?", [thread_id]
            if tenant_id:
                sql += " AND tenant_id = ?"
                args.append(tenant_id)
            return [r["tenant_id"] for r in await tx.fetchall(sql, *args)]

        return await self._store.run(read)

    async def steps(
        self, tenant_id: str, thread_id: str, page_size: int, page_token: str | None
    ) -> tuple[list[dict[str, Any]], str | None]:
        """The thread's durable journal (live token streams excluded), run by run, entry by entry."""
        if not await self._store.has(RUNTIME):
            return [], None
        where, args = ["r.tenant = ?", "r.thread_id = ?", "e.ephemeral = 0"], [tenant_id, thread_id]
        if page_token:
            run_id, seq = _untoken(page_token, 2)
            where.append("(e.run_id, e.seq) > (?, ?)")
            args += [str(run_id), int(seq)]

        async def read(tx: Tx) -> list:
            return list(
                await tx.fetchall(
                    "SELECT e.run_id AS run_id, e.seq AS seq, e.entry_json AS entry FROM rt_events e "  # noqa: S608
                    f"JOIN rt_runs r ON r.run_id = e.run_id WHERE {' AND '.join(where)} "
                    "ORDER BY e.run_id, e.seq LIMIT ?",
                    *args,
                    page_size + 1,
                )
            )

        rows = await self._store.run(read)
        more = len(rows) > page_size
        rows = rows[:page_size]
        items = [json.loads(r["entry"]) for r in rows]
        last = rows[-1] if rows else None
        return items, (_token(last["run_id"], last["seq"]) if more else None)

    async def drop_header(self, tenant_id: str, thread_id: str) -> int:
        """Remove a thread's header (and tags) for good."""

        async def write(tx: Tx) -> int:
            await tx.execute(
                "DELETE FROM thread_header_tags WHERE tenant_id = ? AND thread_id = ?",
                tenant_id,
                thread_id,
            )
            return await tx.execute(
                "DELETE FROM thread_headers WHERE tenant_id = ? AND id = ?", tenant_id, thread_id
            )

        return await self._store.run(write)
