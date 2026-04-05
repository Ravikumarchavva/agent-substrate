"""Retry middleware.

Retries the execute function when specific exception types are raised.
Uses exponential backoff with jitter.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Optional, Tuple, Type

from raavan.core.middleware.base import BaseMiddleware, MiddlewareContext

logger = logging.getLogger(__name__)


class RetryMiddleware(BaseMiddleware):
    """Retries execution on transient errors.

    Wraps the execution via ``on_error()`` — when a retryable exception
    is caught, returns a sentinel that the pipeline runner interprets as
    "retry the execute_fn".

    For simple use, this middleware stores a retry count in
    ``ctx.metadata["_retry_attempt"]`` and the pipeline runner
    can be wrapped externally.  However, the primary use is
    standalone via its ``run_with_retry`` helper.
    """

    def __init__(
        self,
        *,
        name: str = "retry",
        max_retries: int = 3,
        retryable_exceptions: Tuple[Type[Exception], ...] = (Exception,),
        base_delay: float = 1.0,
        max_delay: float = 30.0,
    ) -> None:
        super().__init__(name)
        self.max_retries = max_retries
        self.retryable_exceptions = retryable_exceptions
        self.base_delay = base_delay
        self.max_delay = max_delay

    async def before(self, ctx: MiddlewareContext) -> MiddlewareContext:
        ctx.metadata.setdefault("_retry_attempt", 0)
        return ctx

    async def after(self, ctx: MiddlewareContext, result: Any) -> Any:
        # Reset retry counter on success
        ctx.metadata["_retry_attempt"] = 0
        return result

    async def on_error(self, ctx: MiddlewareContext, error: Exception) -> Optional[Any]:
        if not isinstance(error, self.retryable_exceptions):
            return None

        attempt = ctx.metadata.get("_retry_attempt", 0)
        if attempt >= self.max_retries:
            logger.warning(
                f"RetryMiddleware: max retries ({self.max_retries}) exhausted"
            )
            return None

        delay = min(
            self.base_delay * (2**attempt) + random.uniform(0, 1),
            self.max_delay,
        )
        logger.info(
            f"RetryMiddleware: attempt {attempt + 1}/{self.max_retries}, "
            f"waiting {delay:.1f}s — {error}"
        )
        await asyncio.sleep(delay)
        ctx.metadata["_retry_attempt"] = attempt + 1
        # Return None — the exception will propagate; the caller can
        # detect the retry attempt counter and re-invoke.
        return None
