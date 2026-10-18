"""Counters: the small shared numbers a server keeps for abuse control and plan limits.

Two shapes, both keyed by a string the caller builds (tenant- or user-prefixed, so one tenant never shares a counter
with another):

* a sliding window (`hit` / `peek`): how many times in the last `window_seconds`, for request rate limits;
* a day counter (`add_day` / `release_day` / `used_day`): how much so far today (UTC), for daily plan quotas.

The in-memory adapter is exact within one process; the Redis adapter shares the numbers between processes
(``loamz.agents.testing.conformance`` style tests cover both).
"""

from __future__ import annotations

from typing import NamedTuple, Protocol, runtime_checkable


class Window(NamedTuple):
    count: int
    reset_in: int
    """Seconds until the oldest hit leaves the window."""


class Day(NamedTuple):
    allowed: bool
    used: int
    remaining: int


@runtime_checkable
class Counters(Protocol):
    async def hit(self, key: str, *, window_seconds: int) -> Window: ...

    async def peek(self, key: str, *, window_seconds: int) -> Window: ...

    async def add_day(self, key: str, *, limit: int, count: int = 1) -> Day: ...

    async def release_day(self, key: str, *, count: int = 1) -> None: ...

    async def used_day(self, key: str, *, limit: int) -> Day: ...


def seconds_until_tomorrow() -> int:
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    midnight = datetime.combine((now + timedelta(days=1)).date(), datetime.min.time(), tzinfo=UTC)
    return max(0, int((midnight - now).total_seconds()))


__all__ = ["Counters", "Day", "Window", "seconds_until_tomorrow"]
