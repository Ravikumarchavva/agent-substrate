from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

from loamz.engine.agents.kernel.ports.counters import Day, Window


def _day(key: str) -> str:
    return f"{key}:{datetime.now(UTC).date().isoformat()}"


class RedisCounters:
    """Counters shared by every process that uses the same Redis (a sliding window in a ZSET, a day in a counter)."""

    def __init__(self, client: Any) -> None:
        self._r = client

    async def hit(self, key: str, *, window_seconds: int) -> Window:
        now = time.time()
        pipe = self._r.pipeline()
        pipe.zremrangebyscore(key, "-inf", now - window_seconds)
        pipe.zadd(key, {f"{now:.6f}:{uuid.uuid4().hex[:8]}": now})
        pipe.zcard(key)
        pipe.expire(key, window_seconds + 1)
        results = await pipe.execute()
        return Window(int(results[2]), window_seconds)

    async def peek(self, key: str, *, window_seconds: int) -> Window:
        now = time.time()
        pipe = self._r.pipeline()
        pipe.zremrangebyscore(key, "-inf", now - window_seconds)
        pipe.zcard(key)
        pipe.zrange(key, 0, 0, withscores=True)
        results = await pipe.execute()
        oldest = results[2]
        reset = (
            window_seconds if not oldest else max(0, int(window_seconds - (now - oldest[0][1])) + 1)
        )
        return Window(int(results[1]), reset)

    async def add_day(self, key: str, *, limit: int, count: int = 1) -> Day:
        k = _day(key)
        used = int(await self._r.incrby(k, count))
        if used == count:
            await self._r.expire(k, 86400)
        return Day(used <= limit, used, max(0, limit - used))

    async def release_day(self, key: str, *, count: int = 1) -> None:
        k = _day(key)
        if int(await self._r.decrby(k, count)) < 0:
            await self._r.set(k, 0, keepttl=True)

    async def used_day(self, key: str, *, limit: int) -> Day:
        raw = await self._r.get(_day(key))
        used = int(raw) if raw else 0
        return Day(used <= limit, used, max(0, limit - used))


__all__ = ["RedisCounters"]
