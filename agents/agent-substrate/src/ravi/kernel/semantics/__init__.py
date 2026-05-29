"""ravi.kernel.semantics — Semantic-invariant and divergence contracts.

Pure contracts (Protocols + value objects + enums). Concrete checkers and
divergence detectors live in :mod:`ravi.guardrails.semantic`.
"""

from __future__ import annotations

from ravi.kernel.semantics._contracts import (
    InvariantEvaluationResult,
    SemanticDivergence,
    SemanticDivergenceDetector,
    SemanticInvariant,
    SemanticInvariantChecker,
    SemanticInvariantKind,
    SemanticPayload,
    SemanticSeverity,
)

__all__ = [
    "InvariantEvaluationResult",
    "SemanticDivergence",
    "SemanticDivergenceDetector",
    "SemanticInvariant",
    "SemanticInvariantChecker",
    "SemanticInvariantKind",
    "SemanticPayload",
    "SemanticSeverity",
]
