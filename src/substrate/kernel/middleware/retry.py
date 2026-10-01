from __future__ import annotations

import asyncio
import random
from typing import Callable, Awaitable, ClassVar

from substrate.agents.llm.errors import classify_llm_error
from substrate.kernel.exceptions import KernelError, PermanentError, TransientError
from substrate.logger import setup_logging
from substrate.agents.middleware._contracts import MiddlewareContext
from substrate.kernel.agent.middleware import MiddlewareStage

logger = setup_logging()


def _worth_retrying(exc: Exception) -> bool:
    if isinstance(exc, KernelError):
        return isinstance(exc, TransientError)
    return not isinstance(classify_llm_error(exc), PermanentError)


def _backoff(attempt: int, base: float, max_delay: float, jitter: float) -> float:
    delay = min(base * (2**attempt), max_delay)
    return delay + random.uniform(0, jitter)


class RetryMiddleware:
    """Retries LLM execution on transient errors using exponential backoff.

    Never retries what can't succeed on a second try: a kernel error that is
    not transient (a guardrail or budget stop) or a request the provider will
    always reject (bad key, malformed request, context too long).
    """

    stages: ClassVar[frozenset[MiddlewareStage]] = frozenset({MiddlewareStage.CHAT})

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

    async def process(
        self, context: MiddlewareContext, call_next: Callable[[], Awaitable[None]]
    ) -> None:
        attempt = 0
        while True:
            try:
                await call_next()
                return
            except self.retryable_exceptions as exc:
                if not _worth_retrying(exc):
                    raise
                if attempt >= self.max_retries:
                    logger.warning(
                        "RetryMiddleware: max retries (%d) exhausted", self.max_retries
                    )
                    raise

                delay = _backoff(attempt, self.base_delay, self.max_delay, self.jitter)
                logger.info(
                    "RetryMiddleware: attempt %d/%d, waiting %.1fs — %s",
                    attempt + 1,
                    self.max_retries,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
                attempt += 1
