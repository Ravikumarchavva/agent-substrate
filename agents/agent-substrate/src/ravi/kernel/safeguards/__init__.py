"""ravi.kernel.safeguards — Self-mutation and circuit-breaker contracts.

Pure contracts (Protocols + value objects + enums). Concrete policies and
breakers live in :mod:`ravi.guardrails.mutation`.
"""

from __future__ import annotations

from ravi.kernel.safeguards._breaker import (
    BreakerSnapshot,
    BreakerState,
    CircuitBreaker,
    CircuitOpen,
)
from ravi.kernel.safeguards._mutation import (
    MutationKind,
    MutationPermission,
    MutationPolicy,
    MutationRequest,
)

__all__ = [
    "BreakerSnapshot",
    "BreakerState",
    "CircuitBreaker",
    "CircuitOpen",
    "MutationKind",
    "MutationPermission",
    "MutationPolicy",
    "MutationRequest",
]
