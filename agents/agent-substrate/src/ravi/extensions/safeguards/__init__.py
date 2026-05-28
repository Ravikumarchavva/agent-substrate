"""Reference implementations for self-evolution safeguard contracts."""

from __future__ import annotations

from ravi.extensions.safeguards._in_memory import (
    DEFAULT_FORBIDDEN_MUTATION_KINDS,
    InMemoryCircuitBreaker,
    InMemoryMutationPolicy,
)

__all__ = [
    "DEFAULT_FORBIDDEN_MUTATION_KINDS",
    "InMemoryCircuitBreaker",
    "InMemoryMutationPolicy",
]
