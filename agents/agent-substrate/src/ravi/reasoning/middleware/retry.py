"""Retry middleware.

Retries the execute function when specific exception types are raised.
Uses exponential backoff with jitter.

Canonical retry math lives in ``ravi.guardrails.resilience.policies``.
``RetryMiddleware`` is middleware-pipeline sugar over that same policy.
"""

from __future__ import annotations
from ravi.logger import setup_logging

import asyncio
from typing import Any, Optional, Tuple, Type
import warnings

from ravi.kernel.middleware.base import BaseMiddleware, MiddlewareContext
from ravi.guardrails.resilience.policies import RetryPolicy, _calculate_delay

logger = setup_logging()


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
        policy: Optional[RetryPolicy] = None,
        max_retries: int = 3,
        retryable_exceptions: Tuple[Type[Exception], ...] = (Exception,),
        base_delay: float = 1.0,
        max_delay: float = 30.0,
    ) -> None:
        super().__init__(name)
        if policy is not None:
            self._policy = policy
        else:
            self._policy = RetryPolicy(
                max_retries=max_retries,
                base_delay=base_delay,
                max_delay=max_delay,
                backoff_factor=2.0,
                jitter=1.0,
                retryable_exceptions=retryable_exceptions,
            )
        self.max_retries = self._policy.max_retries
        self.retryable_exceptions = self._policy.retryable_exceptions
        self.base_delay = self._policy.base_delay
        self.max_delay = self._policy.max_delay
        if policy is None and (
            max_retries != 3
            or retryable_exceptions != (Exception,)
            or base_delay != 1.0
            or max_delay != 30.0
        ):
            warnings.warn(
                "RetryMiddleware legacy constructor fields are deprecated; "
                "pass policy=RetryPolicy(...) instead.",
                DeprecationWarning,
                stacklevel=2,
            )

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

        delay = _calculate_delay(attempt, self._policy)
        logger.info(
            f"RetryMiddleware: attempt {attempt + 1}/{self.max_retries}, "
            f"waiting {delay:.1f}s — {error}"
        )
        await asyncio.sleep(delay)
        ctx.metadata["_retry_attempt"] = attempt + 1
        # Return None — the exception will propagate; the caller can
        # detect the retry attempt counter and re-invoke.
        return None
