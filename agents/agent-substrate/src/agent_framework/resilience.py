"""Retry and resilience utilities for production agent workloads.

Provides:
  - retry_async: Decorator for async functions with exponential backoff + jitter.
  - RetryPolicy: Configurable retry parameters.
  - CircuitBreaker: Fail-fast when a dependency is unhealthy.

Design decisions:
  - Pure async — no threading overhead.
  - Jitter prevents thundering herd on shared LLM endpoints.
  - Retryable errors are detected by exception class, not status code,
    keeping the utility transport-agnostic.
  - CircuitBreaker tracks failure rate over a sliding window.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, Set, Tuple, Type

logger = logging.getLogger("agent_framework.resilience")


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
    delay = policy.base_delay * (policy.backoff_factor ** attempt)
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
                    # Non-retryable — propagate immediately
                    raise

            # Should not reach here, but safety
            if last_exception:
                raise last_exception

        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------

class CircuitState(str, Enum):
    CLOSED = "closed"       # Normal operation
    OPEN = "open"           # Failing — reject calls immediately
    HALF_OPEN = "half_open" # Testing recovery


class CircuitBreakerOpenError(Exception):
    """Raised when a call is rejected because the circuit is open."""
    pass


@dataclass
class CircuitBreaker:
    """Fail-fast when a dependency is persistently unhealthy.

    Usage::

        breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30.0)

        async def call_llm():
            async with breaker:
                return await model_client.generate(...)

    State machine:
        CLOSED → (failures >= threshold) → OPEN
        OPEN   → (after recovery_timeout) → HALF_OPEN
        HALF_OPEN → (success) → CLOSED
        HALF_OPEN → (failure) → OPEN
    """
    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    monitored_exceptions: Tuple[Type[Exception], ...] = (
        ConnectionError,
        TimeoutError,
        OSError,
    )

    # Internal state
    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)
    _success_count: int = field(default=0, init=False)

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            # Check if recovery timeout has elapsed
            if time.monotonic() - self._last_failure_time >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                logger.info("Circuit breaker → HALF_OPEN (testing recovery)")
        return self._state

    def record_success(self) -> None:
        """Record a successful call."""
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            logger.info("Circuit breaker → CLOSED (recovered)")
        self._success_count += 1

    def record_failure(self) -> None:
        """Record a failed call."""
        self._failure_count += 1
        self._last_failure_time = time.monotonic()

        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            logger.warning("Circuit breaker → OPEN (failed during recovery)")
        elif self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            logger.warning(
                f"Circuit breaker → OPEN "
                f"({self._failure_count} failures >= {self.failure_threshold})"
            )

    async def __aenter__(self):
        if self.state == CircuitState.OPEN:
            raise CircuitBreakerOpenError(
                f"Circuit breaker is OPEN (failed {self._failure_count} times). "
                f"Recovery in {self.recovery_timeout - (time.monotonic() - self._last_failure_time):.1f}s"
            )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.record_success()
        elif issubclass(exc_type, self.monitored_exceptions):
            self.record_failure()
        return False  # Don't suppress exceptions

    def reset(self) -> None:
        """Manually reset the circuit breaker."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = 0.0

    def stats(self) -> dict:
        """Return current stats."""
        return {
            "state": self.state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
        }
