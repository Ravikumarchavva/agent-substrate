"""Semantic consistency contracts for deterministic invariant checks.

The kernel defines only value objects and Protocols. Concrete checkers,
history, retention, and storage live in ``ravi.extensions.semantics``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, IntEnum
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable
import uuid

UTC = timezone.utc


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SemanticInvariantKind(Enum):
    """Deterministic invariant families understood by reference checkers."""

    FIELD_EXISTS = "field_exists"
    FIELD_EQUALS = "field_equals"
    NUMERIC_RANGE = "numeric_range"
    MONOTONIC_SEQUENCE = "monotonic_sequence"
    CUSTOM_PREDICATE = "custom_predicate"


class SemanticPayload(Enum):
    """Which payload an invariant reads by default."""

    STATE = "state"
    EVENT = "event"


class SemanticSeverity(IntEnum):
    """Operator-facing severity for failed invariants."""

    INFO = 10
    WARNING = 20
    ERROR = 30
    CRITICAL = 40

    def at_least(self, minimum: SemanticSeverity) -> bool:
        """Return true when this severity is at least ``minimum``."""
        return self >= minimum


SemanticPredicate = Callable[[Mapping[str, Any], Mapping[str, Any]], bool]


_FIELD_KINDS = frozenset(
    {
        SemanticInvariantKind.FIELD_EXISTS,
        SemanticInvariantKind.FIELD_EQUALS,
        SemanticInvariantKind.NUMERIC_RANGE,
        SemanticInvariantKind.MONOTONIC_SEQUENCE,
    }
)


@dataclass(frozen=True, slots=True)
class SemanticInvariant:
    """Declarative rule that a semantic checker can evaluate.

    ``field_path`` uses implementation-defined dotted path semantics. The
    in-memory reference checker supports mapping keys and sequence indexes.
    """

    invariant_id: str
    kind: SemanticInvariantKind
    field_path: str | None = None
    target: SemanticPayload = SemanticPayload.EVENT
    severity: SemanticSeverity = SemanticSeverity.ERROR
    description: str = ""
    expected: Any = None
    min_value: float | None = None
    max_value: float | None = None
    allow_equal: bool = True
    subject_path: str | None = None
    predicate: SemanticPredicate | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.invariant_id:
            raise ValueError("SemanticInvariant.invariant_id must be non-empty")
        if self.kind in _FIELD_KINDS and not self.field_path:
            raise ValueError(f"{self.kind.value} invariants require field_path")
        if self.kind is SemanticInvariantKind.NUMERIC_RANGE:
            if self.min_value is None and self.max_value is None:
                raise ValueError("numeric_range requires min_value or max_value")
            if (
                self.min_value is not None
                and self.max_value is not None
                and self.min_value > self.max_value
            ):
                raise ValueError("numeric_range min_value cannot exceed max_value")
        if (
            self.kind is SemanticInvariantKind.CUSTOM_PREDICATE
            and self.predicate is None
        ):
            raise ValueError("custom_predicate invariants require predicate")
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )


@dataclass(frozen=True, slots=True)
class InvariantEvaluationResult:
    """Result of evaluating one :class:`SemanticInvariant`."""

    invariant_id: str
    passed: bool
    severity: SemanticSeverity = SemanticSeverity.ERROR
    message: str = ""
    actual: Any = None
    expected: Any = None
    checked_at: datetime = field(default_factory=_utcnow)
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.invariant_id:
            raise ValueError("InvariantEvaluationResult.invariant_id must be non-empty")
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))


@dataclass(frozen=True, slots=True)
class SemanticDivergence:
    """Signal emitted when an invariant evaluation fails."""

    invariant_id: str
    severity: SemanticSeverity
    message: str
    divergence_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    subject_id: str | None = None
    result: InvariantEvaluationResult | None = None
    detected_at: datetime = field(default_factory=_utcnow)
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.invariant_id:
            raise ValueError("SemanticDivergence.invariant_id must be non-empty")
        if not self.message:
            raise ValueError("SemanticDivergence.message must be non-empty")
        if self.result is not None and self.result.invariant_id != self.invariant_id:
            raise ValueError("SemanticDivergence.result invariant_id mismatch")
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))


@runtime_checkable
class SemanticInvariantChecker(Protocol):
    """Evaluate semantic invariants over state and event payloads."""

    def evaluate(
        self,
        invariant: SemanticInvariant,
        state: Mapping[str, Any] | None = None,
        event: Mapping[str, Any] | None = None,
        *,
        subject_id: str | None = None,
    ) -> InvariantEvaluationResult:
        """Evaluate one invariant."""
        ...

    def evaluate_many(
        self,
        invariants: Iterable[SemanticInvariant],
        state: Mapping[str, Any] | None = None,
        event: Mapping[str, Any] | None = None,
        *,
        subject_id: str | None = None,
    ) -> tuple[InvariantEvaluationResult, ...]:
        """Evaluate a batch of invariants."""
        ...


@runtime_checkable
class SemanticDivergenceDetector(Protocol):
    """Detect, retain, and query semantic divergences."""

    def detect(
        self,
        invariants: Iterable[SemanticInvariant],
        state: Mapping[str, Any] | None = None,
        event: Mapping[str, Any] | None = None,
        *,
        subject_id: str | None = None,
    ) -> tuple[SemanticDivergence, ...]:
        """Evaluate invariants and return failures as divergence signals."""
        ...

    def recent_divergences(
        self,
        *,
        invariant_id: str | None = None,
        severity_at_least: SemanticSeverity | None = None,
        subject_id: str | None = None,
        limit: int | None = None,
    ) -> tuple[SemanticDivergence, ...]:
        """Return retained divergence records, newest first."""
        ...

    def clear_divergences(self) -> int:
        """Clear retained divergences and return how many were removed."""
        ...


__all__ = [
    "InvariantEvaluationResult",
    "SemanticDivergence",
    "SemanticDivergenceDetector",
    "SemanticInvariant",
    "SemanticInvariantChecker",
    "SemanticInvariantKind",
    "SemanticPayload",
    "SemanticPredicate",
    "SemanticSeverity",
]
