"""Token-bucket rate limiter middleware.

Limits the rate of LLM calls or tool executions per agent to prevent
runaway loops and excessive API costs.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from raavan.core.middleware.base import BaseMiddleware, MiddlewareContext


class RateLimiterMiddleware(BaseMiddleware):
    """Token-bucket rate limiter.

    Defaults to 60 requests per minute.  ``before()`` blocks until a
    token is available.
    """

    def __init__(
        self,
        *,
        name: str = "rate_limiter",
        max_rate: float = 60.0,
        per_seconds: float = 60.0,
    ) -> None:
        super().__init__(name)
        self._max_tokens = max_rate
        self._refill_rate = max_rate / per_seconds
        self._tokens = max_rate
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def before(self, ctx: MiddlewareContext) -> MiddlewareContext:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(
                self._max_tokens,
                self._tokens + elapsed * self._refill_rate,
            )
            self._last_refill = now

            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self._refill_rate
                await asyncio.sleep(wait)
                self._tokens = 0.0
            else:
                self._tokens -= 1.0

        return ctx

    async def after(self, ctx: MiddlewareContext, result: Any) -> Any:
        return result
