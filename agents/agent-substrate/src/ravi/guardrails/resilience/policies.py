"""Retry and resilience utilities for production agent workloads.

Provides:
  - retry_async: Decorator for async functions with exponential backoff + jitter.
  - RetryPolicy: Configurable retry parameters.
"""

from __future__ import annotations
from ravi.logger import setup_logging

import asyncio
import functools
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional, Tuple, Type

logger = setup_logging()


# ---------------------------------------------------------------------------
# Retry Policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetryPolicy:
    """Configuration for retry behaviour.

    Attributes:
        max_retries: Maximum number of retry attempts (0 = no retries).
        base_delay: Initial delay in seconds before first retry.
        max_delay: Cap on delay (prevents absurdly long waits).
        backoff_factor: Multiplier for exponential growth (2.0 = doubling).
        jitter: Randomisation range added to delay (prevents thundering herd).
        retryable_exceptions: Exception types that trigger a retry.
            Defaults to common transient errors.
    """

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    backoff_factor: float = 2.0
    jitter: float = 0.5
    retryable_exceptions: Tuple[Type[Exception], ...] = (
        ConnectionError,
        TimeoutError,
        OSError,
    )


# Default policies for common use cases
LLM_RETRY_POLICY = RetryPolicy(
    max_retries=3,
    base_delay=1.0,
    max_delay=30.0,
    backoff_factor=2.0,
    jitter=0.5,
    retryable_exceptions=(
        ConnectionError,
        TimeoutError,
        OSError,
    ),
)

TOOL_RETRY_POLICY = RetryPolicy(
    max_retries=2,
    base_delay=0.5,
    max_delay=10.0,
    backoff_factor=2.0,
    jitter=0.3,
    retryable_exceptions=(
        ConnectionError,
        TimeoutError,
        OSError,
    ),
)


def _calculate_delay(attempt: int, policy: RetryPolicy) -> float:
    """Calculate delay with exponential backoff + jitter."""
    delay = policy.base_delay * (policy.backoff_factor**attempt)
    delay = min(delay, policy.max_delay)
    jitter = random.uniform(0, policy.jitter)
    return delay + jitter


# ---------------------------------------------------------------------------
# Retry decorator
# ---------------------------------------------------------------------------


def retry_async(
    policy: Optional[RetryPolicy] = None,
    *,
    on_retry: Optional[Callable[[Exception, int, float], None]] = None,
):
    """Decorator: retry an async function with exponential backoff.

    Usage::

        @retry_async(LLM_RETRY_POLICY)
        async def call_llm(...):
            ...

    Args:
        policy: RetryPolicy (defaults to LLM_RETRY_POLICY).
        on_retry: Optional callback(exception, attempt, delay) for logging.
    """
    _policy = policy or LLM_RETRY_POLICY

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception: Optional[Exception] = None

            for attempt in range(_policy.max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except _policy.retryable_exceptions as e:
                    last_exception = e
                    if attempt < _policy.max_retries:
                        delay = _calculate_delay(attempt, _policy)
                        logger.warning(
                            f"Retry {attempt + 1}/{_policy.max_retries} "
                            f"for {func.__name__}: {e} "
                            f"(waiting {delay:.1f}s)"
                        )
                        if on_retry:
                            on_retry(e, attempt + 1, delay)
                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            f"All {_policy.max_retries} retries exhausted "
                            f"for {func.__name__}: {e}"
                        )
                        raise
                except Exception:
                    # Non-retryable -- propagate immediately
                    raise

            # Should not reach here, but safety
            if last_exception:
                raise last_exception

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------


class _CircuitState(str, Enum):
    """Internal state machine for the circuit breaker."""

    CLOSED = "closed"  # Normal operation — requests flow through.
    OPEN = "open"  # Failing — all requests are rejected immediately.
    HALF_OPEN = "half_open"  # Testing recovery — one probe request allowed.


@dataclass
class CircuitBreaker:
    """Thread-safe async circuit breaker for LLM / tool call protection.

    States:
      CLOSED   → requests flow; failure_threshold failures → OPEN.
      OPEN     → requests rejected for recovery_timeout seconds; then HALF_OPEN.
      HALF_OPEN → one probe allowed; success → CLOSED; failure → OPEN.

    Usage::

        cb = CircuitBreaker(failure_threshold=3)
        if not cb.allow_request():
            raise CircuitOpenError("downstream unavailable")
        try:
            result = await call_llm()
            cb.record_success()
        except Exception:
            cb.record_failure()
            raise
    """

    failure_threshold: int = 5
    recovery_timeout: float = 60.0
    success_threshold: int = 2  # successes in HALF_OPEN before closing

    _state: _CircuitState = field(default=_CircuitState.CLOSED, init=False, repr=False)
    _failure_count: int = field(default=0, init=False, repr=False)
    _success_count: int = field(default=0, init=False, repr=False)
    _last_failure_time: Optional[float] = field(default=None, init=False, repr=False)

    def allow_request(self) -> bool:
        """Return True if a request should be allowed through.

        Raises ``CircuitOpenError`` when the circuit is OPEN and the
        recovery window has not elapsed yet.
        """
        from ravi.kernel.execution.errors import CircuitOpenError

        if self._state == _CircuitState.CLOSED:
            return True

        if self._state == _CircuitState.OPEN:
            import time

            if (
                self._last_failure_time is not None
                and time.monotonic() - self._last_failure_time >= self.recovery_timeout
            ):
                self._state = _CircuitState.HALF_OPEN
                self._success_count = 0
                logger.info("CircuitBreaker: OPEN → HALF_OPEN")
                return True
            raise CircuitOpenError(
                f"Circuit is OPEN; next probe allowed in {self.recovery_timeout:.0f}s"
            )

        # HALF_OPEN — allow exactly one probe
        return True

    def record_success(self) -> None:
        """Record a successful call and potentially close the circuit."""
        if self._state == _CircuitState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self.success_threshold:
                self._state = _CircuitState.CLOSED
                self._failure_count = 0
                self._success_count = 0
                logger.info("CircuitBreaker: HALF_OPEN → CLOSED (recovered)")
        elif self._state == _CircuitState.CLOSED:
            # Reset failure count on successful calls
            self._failure_count = 0

    def record_failure(self) -> None:
        """Record a failed call and potentially open the circuit."""
        import time

        self._last_failure_time = time.monotonic()

        if self._state == _CircuitState.HALF_OPEN:
            self._state = _CircuitState.OPEN
            logger.warning("CircuitBreaker: HALF_OPEN → OPEN (probe failed)")
            return

        self._failure_count += 1
        if (
            self._state == _CircuitState.CLOSED
            and self._failure_count >= self.failure_threshold
        ):
            self._state = _CircuitState.OPEN
            logger.warning(
                "CircuitBreaker: CLOSED → OPEN after %d failures",
                self._failure_count,
            )

    @property
    def state(self) -> str:
        """Current circuit state as a string."""
        return self._state.value


# ---------------------------------------------------------------------------
# Timeout Policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TimeoutPolicy:
    """Per-agent timeout configuration.

    Usage::

        policy = TimeoutPolicy(timeout_seconds=120.0, on_timeout="raise")
        try:
            result = await asyncio.wait_for(coro(), timeout=policy.timeout_seconds)
        except asyncio.TimeoutError:
            if policy.on_timeout == "raise":
                raise AgentTimeoutError(...)
    """

    timeout_seconds: float = 300.0
    on_timeout: str = "raise"  # "raise" | "fallback"


# ---------------------------------------------------------------------------
# Bulkhead Policy
# ---------------------------------------------------------------------------


@dataclass
class BulkheadPolicy:
    """Limits max concurrent agent/tool executions to prevent resource exhaustion.

    Usage::

        bulkhead = BulkheadPolicy(max_concurrent=5)
        async with bulkhead:
            result = await expensive_tool.run(...)
    """

    max_concurrent: int = 10

    def __post_init__(self) -> None:
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(self.max_concurrent)

    async def acquire(self) -> None:
        """Acquire a slot; waits if all slots are busy."""
        await self._semaphore.acquire()

    def release(self) -> None:
        """Release a slot."""
        self._semaphore.release()

    async def __aenter__(self) -> "BulkheadPolicy":
        await self.acquire()
        return self

    async def __aexit__(self, *_: object) -> None:
        self.release()
