"""Circuit-breaker contracts for self-evolution safeguards.

Self-evolution failures are noisy by nature: a bad prompt rewrite can trigger
tool errors, safety denials, or repeated mutation attempts in a tight loop.
The kernel defines a small breaker contract so runtimes can isolate a
misbehaving principal before it can hammer the rest of the fabric.

This module is contract-only. Concrete storage and state-machine
implementations live in :mod:`ravi.extensions.safeguards`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Protocol, runtime_checkable

__all__ = [
    "BreakerSnapshot",
    "BreakerState",
    "CircuitBreaker",
    "CircuitOpen",
]


class BreakerState(Enum):
    """State of a circuit breaker for one principal."""

    CLOSED = auto()      # Normal operation; requests are allowed.
    OPEN = auto()        # Requests are rejected until the recovery window ends.
    HALF_OPEN = auto()   # A bounded probe is allowed to test recovery.


@dataclass(frozen=True, slots=True)
class BreakerSnapshot:
    """Point-in-time view of a principal's circuit breaker."""

    principal_fqn: str
    state: BreakerState
    failure_count: int
    success_count: int
    failure_threshold: int
    success_threshold: int
    updated_at: str  # ISO-8601
    opened_at: str | None = None  # ISO-8601
    retry_after_seconds: float | None = None


class CircuitOpen(RuntimeError):
    """Raised when :class:`CircuitBreaker` rejects a request."""

    def __init__(
        self,
        *,
        principal_fqn: str,
        state: BreakerState,
        reason: str,
        opened_at: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        self.principal_fqn = principal_fqn
        self.state = state
        self.reason = reason
        self.opened_at = opened_at
        self.retry_after_seconds = retry_after_seconds

        retry = ""
        if retry_after_seconds is not None:
            retry = f"; retry_after_seconds={retry_after_seconds:.3f}"
        super().__init__(
            f"circuit open for {principal_fqn}: {reason}{retry}"
        )


@runtime_checkable
class CircuitBreaker(Protocol):
    """Per-principal breaker contract for self-evolution runtime guards."""

    async def allow_request(self, principal_fqn: str) -> BreakerSnapshot:
        """Return a snapshot when allowed; raise :class:`CircuitOpen` otherwise."""
        ...

    async def record_success(self, principal_fqn: str) -> BreakerSnapshot:
        """Record a successful guarded operation and return the new state."""
        ...

    async def record_failure(self, principal_fqn: str) -> BreakerSnapshot:
        """Record a failed guarded operation and return the new state."""
        ...

    async def reset(self, principal_fqn: str) -> BreakerSnapshot:
        """Reset ``principal_fqn`` to the closed state."""
        ...

    async def state_for(self, principal_fqn: str) -> BreakerSnapshot:
        """Return the current state snapshot for ``principal_fqn``."""
        ...
