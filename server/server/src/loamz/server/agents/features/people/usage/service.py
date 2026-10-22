from __future__ import annotations

from datetime import UTC, datetime, timedelta

from loamz.engine.agents.kernel.caller import Caller
from loamz.engine.agents.stores import AgentStore
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.features.people.usage.model import DayOut, Totals, UsageOut
from loamz.server.agents.features.people.usage.store import MAX_THREADS, usage_by_day

__all__ = ["UsageOut", "UsageService", "usage"]


def _sum(days: list[DayOut]) -> Totals:
    return Totals(
        messages=sum(d.messages for d in days),
        tokens=sum(d.tokens for d in days),
        cost_usd=round(sum(d.cost_usd for d in days), 6),
    )


class UsageService:
    """What the caller has used, by day, so a limit or a bill is never a surprise."""

    def __init__(self, store: AgentStore) -> None:
        self._store = store

    async def get(self, caller: Caller, days: int = 30) -> UsageOut:
        now = datetime.now(UTC)
        since = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
        thread_ids = await self._store.thread_headers.owned_ids(
            caller.tenant_id, caller.user_id, limit=MAX_THREADS
        )
        series = await usage_by_day(
            self._store, tenant=caller.tenant_id, thread_ids=thread_ids, since=since
        )
        out = [
            DayOut(
                date=d.day,
                messages=d.messages,
                calls=d.calls,
                tokens=d.tokens,
                cost_usd=round(d.cost_usd, 6),
            )
            for d in series
        ]
        today = now.date()
        return UsageOut(
            days=out,
            today=_sum([d for d in out if d.date == today]),
            this_month=_sum(
                [d for d in out if d.date.year == today.year and d.date.month == today.month]
            ),
            window=_sum(out),
            note="Counts every model call in your conversations, scheduled tasks included. UTC days.",
        )

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        """Usage is computed from the run journal, which erasure clears with the conversations: nothing is kept here."""
        return 0


def usage(container: Container) -> UsageService:
    """Provider: how the container builds this feature's service."""
    return UsageService(container.agent_store)
