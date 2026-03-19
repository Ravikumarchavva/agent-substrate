"""Retry and resilience utilities for production agent workloads.

Provides:
  - retry_async: Decorator for async functions with exponential backoff + jitter.
  - RetryPolicy: Configurable retry parameters.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import random
from dataclasses import dataclass
from typing import Callable, Optional, Tuple, Type

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
                    # Non-retryable -- propagate immediately
                    raise

            # Should not reach here, but safety
            if last_exception:
                raise last_exception

        return wrapper
    return decorator
