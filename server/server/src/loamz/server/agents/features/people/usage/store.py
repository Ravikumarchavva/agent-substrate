"""What a person has used: messages, tokens and cost by day, read from the runs' journals.

Every model call a run makes is journalled (`llm.call` with its tokens and cost) and every message a person sends is too
(`user.message`), so usage is a sum over the journal of the person's own conversations rather than a second counter kept
in step with it. The thread ids come from the caller's ownership-checked query, never from a request.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from loamz.engine.agents.stores import AgentStore
from loamz.engine.sql import Tx

MAX_THREADS = 500
MAX_ENTRIES = 50_000
RUNTIME_COMPONENT = "runtime"
"""The migration component of the run journal (`kernel.runtime.persistence`); without it nothing was ever run."""


@dataclass
class DayUsage:
    day: date
    messages: int = 0
    calls: int = 0
    tokens: int = 0
    cost_usd: float = 0.0


async def usage_by_day(
    store: AgentStore, *, tenant: str, thread_ids: list[str], since: datetime
) -> list[DayUsage]:
    """One entry per UTC day from `since` to today (days without use are zeros, so a chart has no gaps), oldest first."""
    ids = thread_ids[:MAX_THREADS]
    days: dict[date, DayUsage] = defaultdict(lambda: DayUsage(day=date.min))
    if ids and await store.has(RUNTIME_COMPONENT):
        marks = ",".join("?" * len(ids))

        async def read(tx: Tx) -> list:
            return list(
                await tx.fetchall(
                    "SELECT e.kind AS kind, e.entry_json AS entry FROM rt_events e "
                    "JOIN rt_runs r ON r.run_id = e.run_id "
                    f"WHERE r.tenant = ? AND e.kind IN ('llm.call', 'user.message') AND r.thread_id IN ({marks}) "  # noqa: S608 - placeholders only
                    "ORDER BY e.run_id DESC, e.seq DESC LIMIT ?",
                    tenant,
                    *ids,
                    MAX_ENTRIES,
                )
            )

        for row in await store.run(read):
            try:
                entry = json.loads(row["entry"])
                when = datetime.fromisoformat(str(entry["ts"]).replace("Z", "+00:00"))
            except ValueError, KeyError, TypeError:
                continue
            if when < since:
                continue
            day = when.astimezone(UTC).date()
            bucket = days.setdefault(day, DayUsage(day=day))
            if row["kind"] == "user.message":
                bucket.messages += 1
                continue
            payload = entry.get("payload") or {}
            bucket.calls += 1
            try:
                bucket.tokens += int(payload.get("tokens") or 0)
                bucket.cost_usd += float(payload.get("cost_usd") or 0.0)
            except TypeError, ValueError:
                continue  # a call whose numbers cannot be read still counts as a call, with no cost
    start = since.astimezone(UTC).date()
    today = datetime.now(UTC).date()
    return [
        days.get(start + timedelta(days=i), DayUsage(day=start + timedelta(days=i)))
        for i in range((today - start).days + 1)
    ]
