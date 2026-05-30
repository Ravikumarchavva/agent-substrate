from __future__ import annotations

import asyncio
import random
from typing import Any

from ravi.logger import setup_logging
from ravi.reasoning.middleware._contracts import MiddlewareContext

logger = setup_logging()


def _backoff(attempt: int, base: float, max_delay: float, jitter: float) -> float:
    delay = min(base * (2 ** attempt), max_delay)
    return delay + random.uniform(0, jitter)


class RetryMiddleware:
    """Retries execution on transient errors using exponential backoff."""

    def __init__(
        self,
        *,
        max_retries: int = 3,
        retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        jitter: float = 1.0,
    ) -> None:
        self.max_retries = max_retries
        self.retryable_exceptions = retryable_exceptions
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter = jitter

    async def before(self, ctx: MiddlewareContext) -> MiddlewareContext:
        ctx.metadata.setdefault("_retry_attempt", 0)
        return ctx

    async def after(self, ctx: MiddlewareContext, result: Any) -> Any:
        ctx.metadata["_retry_attempt"] = 0
        return result

    async def on_error(self, ctx: MiddlewareContext, error: Exception) -> None:
        if not isinstance(error, self.retryable_exceptions):
            return
        attempt = ctx.metadata.get("_retry_attempt", 0)
        if attempt >= self.max_retries:
            logger.warning("RetryMiddleware: max retries (%d) exhausted", self.max_retries)
            return
        delay = _backoff(attempt, self.base_delay, self.max_delay, self.jitter)
        logger.info(
            "RetryMiddleware: attempt %d/%d, waiting %.1fs — %s",
            attempt + 1,
            self.max_retries,
            delay,
            error,
        )
        await asyncio.sleep(delay)
        ctx.metadata["_retry_attempt"] = attempt + 1
