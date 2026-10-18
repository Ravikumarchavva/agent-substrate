from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import UTC, datetime

from loamz.agents.kernel.ports.counters import Day, Window


def _day(key: str) -> str:
    return f"{key}:{datetime.now(UTC).date().isoformat()}"


class MemoryCounters:
    """Counters inside one process."""

    def __init__(self) -> None:
        self._windows: dict[str, deque[float]] = defaultdict(deque)
        self._days: dict[str, int] = {}

    def _trim(self, key: str, window_seconds: int) -> deque[float]:
        hits = self._windows[key]
        cutoff = time.time() - window_seconds
        while hits and hits[0] <= cutoff:
            hits.popleft()
        return hits

    @staticmethod
    def _reset(hits: deque[float], window_seconds: int) -> int:
        return (
            window_seconds
            if not hits
            else max(0, int(window_seconds - (time.time() - hits[0])) + 1)
        )

    async def hit(self, key: str, *, window_seconds: int) -> Window:
        hits = self._trim(key, window_seconds)
        hits.append(time.time())
        return Window(len(hits), self._reset(hits, window_seconds))

    async def peek(self, key: str, *, window_seconds: int) -> Window:
        hits = self._trim(key, window_seconds)
        return Window(len(hits), self._reset(hits, window_seconds))

    async def add_day(self, key: str, *, limit: int, count: int = 1) -> Day:
        k = _day(key)
        self._days[k] = self._days.get(k, 0) + count
        used = self._days[k]
        return Day(used <= limit, used, max(0, limit - used))

    async def release_day(self, key: str, *, count: int = 1) -> None:
        k = _day(key)
        self._days[k] = max(0, self._days.get(k, 0) - count)

    async def used_day(self, key: str, *, limit: int) -> Day:
        used = self._days.get(_day(key), 0)
        return Day(used <= limit, used, max(0, limit - used))


__all__ = ["MemoryCounters"]
