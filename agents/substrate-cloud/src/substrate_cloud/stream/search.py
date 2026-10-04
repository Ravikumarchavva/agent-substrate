"""Reading across a user's conversations: how many messages each has, and which contain some words.

The conversation lives in the runtime's run log (``rt_runs`` / ``rt_events`` in the engine's store), not in the application's tables, so
these questions go to that store. Both take the thread ids the caller already owns (from the application's own ownership-checked query):
a thread id is never accepted from a request here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from substrate.stores import Store

MESSAGE_KINDS = ("user.message", "assistant.message")
MAX_THREADS = 500
SNIPPET_CHARS = 140


@dataclass(frozen=True)
class Hit:
    thread_id: str
    role: str
    """``user`` or ``assistant``."""
    snippet: str
    seq: int


def _marks(n: int) -> str:
    return ",".join("?" * n)


def _like(query: str) -> str:
    """``query`` as a case-insensitive substring pattern, with LIKE's own wildcards taken literally."""
    escaped = (
        query.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    return f"%{escaped}%"


def snippet(text: str, query: str) -> str:
    """The words around the first match of ``query`` in ``text``, on one line."""
    flat = " ".join(text.split())
    at = flat.lower().find(query.lower())
    if at < 0:
        return flat[:SNIPPET_CHARS]
    start = max(0, at - SNIPPET_CHARS // 3)
    cut = flat[start : start + SNIPPET_CHARS]
    return (
        ("…" if start else "")
        + cut
        + ("…" if start + SNIPPET_CHARS < len(flat) else "")
    )


async def message_counts(
    store: Store, *, tenant: str, thread_ids: list[str]
) -> dict[str, int]:
    """Messages (what the user said and what the assistant answered) in each of ``thread_ids``."""
    ids = thread_ids[:MAX_THREADS]
    if not ids:
        return {}

    async def op(tx: Any) -> dict[str, int]:
        rows = await tx.fetchall(
            "SELECT r.thread_id AS thread_id, COUNT(*) AS n FROM rt_events e JOIN rt_runs r ON r.run_id = e.run_id "
            f"WHERE r.tenant = ? AND e.kind IN (?, ?) AND r.thread_id IN ({_marks(len(ids))}) GROUP BY r.thread_id",  # noqa: S608 — placeholders only
            tenant, *MESSAGE_KINDS, *ids,
        )  # fmt: skip
        return {r["thread_id"]: int(r["n"]) for r in rows}

    return await store.run(op)


async def search_messages(
    store: Store, *, tenant: str, thread_ids: list[str], query: str, limit: int = 30
) -> list[Hit]:
    """Messages containing ``query`` (case-insensitive) in any of ``thread_ids``, newest first, at most ``limit``.

    The database narrows by a substring match on the stored entry; each candidate is then checked against the decoded message text, so a
    word that only appears in the entry's JSON keys or escapes is not a hit."""
    query = query.strip()
    ids = thread_ids[:MAX_THREADS]
    if len(query) < 2 or not ids:
        return []

    async def op(tx: Any) -> list[Any]:
        return list(
            await tx.fetchall(
                "SELECT r.thread_id AS thread_id, e.seq AS seq, e.kind AS kind, e.entry_json AS entry "
                "FROM rt_events e JOIN rt_runs r ON r.run_id = e.run_id "
                f"WHERE r.tenant = ? AND e.kind IN (?, ?) AND r.thread_id IN ({_marks(len(ids))}) "  # noqa: S608 — placeholders only
                "AND lower(e.entry_json) LIKE ? ESCAPE '\\' ORDER BY e.run_id DESC, e.seq DESC LIMIT ?",
                tenant, *MESSAGE_KINDS, *ids, _like(query), limit * 4,
            )
        )  # fmt: skip

    hits: list[Hit] = []
    for row in await store.run(op):
        try:
            text = str(json.loads(row["entry"]).get("payload", {}).get("text", ""))
        except (ValueError, AttributeError):
            continue
        if query.lower() not in text.lower():
            continue
        hits.append(
            Hit(
                row["thread_id"],
                "user" if row["kind"] == "user.message" else "assistant",
                snippet(text, query),
                int(row["seq"]),
            )
        )
        if len(hits) >= limit:
            break
    return hits


__all__ = ["Hit", "message_counts", "search_messages", "snippet"]
