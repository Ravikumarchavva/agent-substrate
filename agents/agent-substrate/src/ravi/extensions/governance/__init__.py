"""Governance + Political Dynamics reference implementations (Section 11)."""

from __future__ import annotations

from ravi.extensions.governance._in_memory import (
    InMemoryCoalitionDetector,
    InMemoryGovernancePolicy,
    InMemoryQuarantineActuator,
)

__all__ = [
    "InMemoryCoalitionDetector",
    "InMemoryGovernancePolicy",
    "InMemoryQuarantineActuator",
]
