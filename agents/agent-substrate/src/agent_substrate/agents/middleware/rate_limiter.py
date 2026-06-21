from __future__ import annotations

import asyncio
import time
from typing import Callable, Awaitable

from agent_substrate.agents.middleware._contracts import AgentCallContext
from agent_substrate.exceptions import MiddlewareTermination


class RateLimiterMiddleware:
    """Token-bucket rate limiter. Defaults to 60 requests per minute."""

    def __init__(self, *, max_rate: float = 60.0, per_seconds: float = 60.0) -> None:
        self._max_tokens = max_rate
        self._refill_rate = max_rate / per_seconds
        self._tokens = max_rate
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def process(
        self, context: AgentCallContext, call_next: Callable[[], Awaitable[None]]
    ) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(
                self._max_tokens, self._tokens + elapsed * self._refill_rate
            )
            self._last_refill = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
            else:
                raise MiddlewareTermination("Rate limit exceeded")

        await call_next()
