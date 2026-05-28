"""Self-evolution safeguards — kernel contracts.

Contracts that bound and gate an agent's ability to *modify itself*:

- :class:`MutationKind` — classes of self-modification we recognise.
- :class:`MutationRequest` / :class:`MutationPermission` — request / decision
  envelopes carried into a :class:`MutationPolicy`.
- :class:`MutationPolicy` — Protocol every gatekeeper implementation honours.
- :class:`CircuitBreaker` / :class:`BreakerState` / :class:`CircuitOpen` —
  per-principal failure-rate breaker so a misbehaving identity gets isolated
  before it can hammer the rest of the fabric.

Implementations live in :mod:`ravi.extensions.safeguards`; the kernel only
defines the contract so any backend (in-memory, Redis, Postgres) is
swappable behind the same Protocol.
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
