"""RLock-backed reference implementations for semantic consistency."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping, Sequence
import math
from numbers import Real
import threading
from typing import Any

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
    "DEFAULT_DIVERGENCE_RETENTION",
    "DeterministicSemanticInvariantChecker",
    "InMemorySemanticDivergenceDetector",
]


DEFAULT_DIVERGENCE_RETENTION = 256
_GLOBAL_SUBJECT = "__global__"


def _payload_for(
    invariant: SemanticInvariant,
    state: Mapping[str, Any] | None,
    event: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    if invariant.target is SemanticPayload.STATE:
        return state or {}
    return event or {}


def _lookup(payload: Mapping[str, Any], path: str | None) -> tuple[bool, Any]:
    if not path:
        return True, payload

    current: Any = payload
    for part in path.split("."):
        if isinstance(current, Mapping):
            if part not in current:
                return False, None
            current = current[part]
            continue
        if isinstance(current, Sequence) and not isinstance(
            current, str | bytes | bytearray
        ):
            try:
                index = int(part)
            except ValueError:
                return False, None
            if index < 0 or index >= len(current):
                return False, None
            current = current[index]
            continue
        return False, None
    return True, current


def _is_clean_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, Real):
        return False
    return math.isfinite(float(value))


def _subject_for(
    invariant: SemanticInvariant,
    state: Mapping[str, Any] | None,
    event: Mapping[str, Any] | None,
    explicit_subject: str | None,
) -> str:
    if explicit_subject is not None:
        return explicit_subject
    if invariant.subject_path is not None:
        found, value = _lookup(event or {}, invariant.subject_path)
        if not found:
            found, value = _lookup(state or {}, invariant.subject_path)
        if found:
            return str(value)
    return _GLOBAL_SUBJECT


def _result(
    invariant: SemanticInvariant,
    *,
    passed: bool,
    message: str,
    actual: Any = None,
    expected: Any = None,
    details: Mapping[str, Any] | None = None,
) -> InvariantEvaluationResult:
    return InvariantEvaluationResult(
        invariant_id=invariant.invariant_id,
        passed=passed,
        severity=invariant.severity,
        message=message,
        actual=actual,
        expected=expected,
        details=details or {},
    )


class DeterministicSemanticInvariantChecker:
    """Evaluate deterministic invariants over mapping-like payloads.

    The only retained state is the high-water mark for monotonic invariants.
    It is guarded by ``threading.RLock`` for free-threaded runtimes.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._last_seen: dict[tuple[str, str], Real] = {}

    def evaluate(
        self,
        invariant: SemanticInvariant,
        state: Mapping[str, Any] | None = None,
        event: Mapping[str, Any] | None = None,
        *,
        subject_id: str | None = None,
    ) -> InvariantEvaluationResult:
        if invariant.kind is SemanticInvariantKind.FIELD_EXISTS:
            return self._evaluate_field_exists(invariant, state, event)
        if invariant.kind is SemanticInvariantKind.FIELD_EQUALS:
            return self._evaluate_field_equals(invariant, state, event)
        if invariant.kind is SemanticInvariantKind.NUMERIC_RANGE:
            return self._evaluate_numeric_range(invariant, state, event)
        if invariant.kind is SemanticInvariantKind.MONOTONIC_SEQUENCE:
            return self._evaluate_monotonic(
                invariant,
                state,
                event,
                subject_id=subject_id,
            )
        if invariant.kind is SemanticInvariantKind.CUSTOM_PREDICATE:
            return self._evaluate_custom(invariant, state, event)
        return _result(
            invariant,
            passed=False,
            message=f"unsupported invariant kind {invariant.kind!r}",
        )

    def evaluate_many(
        self,
        invariants: Iterable[SemanticInvariant],
        state: Mapping[str, Any] | None = None,
        event: Mapping[str, Any] | None = None,
        *,
        subject_id: str | None = None,
    ) -> tuple[InvariantEvaluationResult, ...]:
        return tuple(
            self.evaluate(
                invariant,
                state,
                event,
                subject_id=subject_id,
            )
            for invariant in invariants
        )

    def clear_monotonic_history(self) -> int:
        """Clear retained monotonic high-water marks."""
        with self._lock:
            count = len(self._last_seen)
            self._last_seen.clear()
            return count

    def _evaluate_field_exists(
        self,
        invariant: SemanticInvariant,
        state: Mapping[str, Any] | None,
        event: Mapping[str, Any] | None,
    ) -> InvariantEvaluationResult:
        payload = _payload_for(invariant, state, event)
        found, value = _lookup(payload, invariant.field_path)
        if found:
            return _result(
                invariant,
                passed=True,
                message=f"field {invariant.field_path!r} exists",
                actual=value,
            )
        return _result(
            invariant,
            passed=False,
            message=f"missing {invariant.target.value} field {invariant.field_path!r}",
        )

    def _evaluate_field_equals(
        self,
        invariant: SemanticInvariant,
        state: Mapping[str, Any] | None,
        event: Mapping[str, Any] | None,
    ) -> InvariantEvaluationResult:
        payload = _payload_for(invariant, state, event)
        found, value = _lookup(payload, invariant.field_path)
        if not found:
            return _result(
                invariant,
                passed=False,
                message=f"missing {invariant.target.value} field {invariant.field_path!r}",
                expected=invariant.expected,
            )
        passed = value == invariant.expected
        return _result(
            invariant,
            passed=passed,
            message=(
                f"field {invariant.field_path!r} matched"
                if passed
                else f"field {invariant.field_path!r} diverged"
            ),
            actual=value,
            expected=invariant.expected,
        )

    def _evaluate_numeric_range(
        self,
        invariant: SemanticInvariant,
        state: Mapping[str, Any] | None,
        event: Mapping[str, Any] | None,
    ) -> InvariantEvaluationResult:
        payload = _payload_for(invariant, state, event)
        found, value = _lookup(payload, invariant.field_path)
        expected = {"min": invariant.min_value, "max": invariant.max_value}
        if not found:
            return _result(
                invariant,
                passed=False,
                message=f"missing {invariant.target.value} field {invariant.field_path!r}",
                expected=expected,
            )
        if not _is_clean_number(value):
            return _result(
                invariant,
                passed=False,
                message=f"field {invariant.field_path!r} is not a finite number",
                actual=value,
                expected=expected,
            )
        too_low = invariant.min_value is not None and value < invariant.min_value
        too_high = invariant.max_value is not None and value > invariant.max_value
        passed = not too_low and not too_high
        return _result(
            invariant,
            passed=passed,
            message=(
                f"field {invariant.field_path!r} is within range"
                if passed
                else f"field {invariant.field_path!r} is outside range"
            ),
            actual=value,
            expected=expected,
        )

    def _evaluate_monotonic(
        self,
        invariant: SemanticInvariant,
        state: Mapping[str, Any] | None,
        event: Mapping[str, Any] | None,
        *,
        subject_id: str | None,
    ) -> InvariantEvaluationResult:
        payload = _payload_for(invariant, state, event)
        found, value = _lookup(payload, invariant.field_path)
        if not found:
            return _result(
                invariant,
                passed=False,
                message=f"missing {invariant.target.value} field {invariant.field_path!r}",
            )
        if not _is_clean_number(value):
            return _result(
                invariant,
                passed=False,
                message=f"field {invariant.field_path!r} is not a finite number",
                actual=value,
            )

        subject = _subject_for(invariant, state, event, subject_id)
        key = (invariant.invariant_id, subject)
        with self._lock:
            previous = self._last_seen.get(key)
            if previous is None:
                self._last_seen[key] = value
                return _result(
                    invariant,
                    passed=True,
                    message=f"field {invariant.field_path!r} initialized",
                    actual=value,
                    details={"subject_id": subject},
                )

            passed = value >= previous if invariant.allow_equal else value > previous
            if passed:
                self._last_seen[key] = value
            return _result(
                invariant,
                passed=passed,
                message=(
                    f"field {invariant.field_path!r} advanced"
                    if passed
                    else f"field {invariant.field_path!r} regressed"
                ),
                actual=value,
                expected=previous,
                details={"previous": previous, "subject_id": subject},
            )

    def _evaluate_custom(
        self,
        invariant: SemanticInvariant,
        state: Mapping[str, Any] | None,
        event: Mapping[str, Any] | None,
    ) -> InvariantEvaluationResult:
        predicate = invariant.predicate
        if predicate is None:
            return _result(
                invariant,
                passed=False,
                message="custom predicate is missing",
            )
        try:
            passed = bool(predicate(state or {}, event or {}))
        except Exception as exc:  # noqa: BLE001 - predicate failures are data
            return _result(
                invariant,
                passed=False,
                message=f"custom predicate raised {type(exc).__name__}",
                details={"error": str(exc), "error_type": type(exc).__name__},
            )
        return _result(
            invariant,
            passed=passed,
            message=(
                "custom predicate passed"
                if passed
                else "custom predicate returned false"
            ),
        )


class InMemorySemanticDivergenceDetector:
    """Detect failed invariants and retain recent divergence records."""

    def __init__(
        self,
        *,
        checker: SemanticInvariantChecker | None = None,
        retention_capacity: int = DEFAULT_DIVERGENCE_RETENTION,
    ) -> None:
        if retention_capacity <= 0:
            raise ValueError("retention_capacity must be > 0")
        self._checker = checker or DeterministicSemanticInvariantChecker()
        self._lock = threading.RLock()
        self._divergences: deque[SemanticDivergence] = deque(
            maxlen=retention_capacity
        )

    def detect(
        self,
        invariants: Iterable[SemanticInvariant],
        state: Mapping[str, Any] | None = None,
        event: Mapping[str, Any] | None = None,
        *,
        subject_id: str | None = None,
    ) -> tuple[SemanticDivergence, ...]:
        results = self._checker.evaluate_many(
            invariants,
            state,
            event,
            subject_id=subject_id,
        )
        divergences = tuple(
            SemanticDivergence(
                invariant_id=result.invariant_id,
                severity=result.severity,
                message=result.message or "semantic invariant failed",
                subject_id=subject_id,
                result=result,
                details=result.details,
            )
            for result in results
            if not result.passed
        )
        if divergences:
            with self._lock:
                self._divergences.extend(divergences)
        return divergences

    def recent_divergences(
        self,
        *,
        invariant_id: str | None = None,
        severity_at_least: SemanticSeverity | None = None,
        subject_id: str | None = None,
        limit: int | None = None,
    ) -> tuple[SemanticDivergence, ...]:
        if limit is not None and limit < 0:
            raise ValueError("limit must be >= 0")

        with self._lock:
            snapshot = list(self._divergences)

        matches: list[SemanticDivergence] = []
        for divergence in reversed(snapshot):
            if invariant_id is not None and divergence.invariant_id != invariant_id:
                continue
            if (
                severity_at_least is not None
                and divergence.severity < severity_at_least
            ):
                continue
            if subject_id is not None and divergence.subject_id != subject_id:
                continue
            matches.append(divergence)
            if limit is not None and len(matches) >= limit:
                break
        return tuple(matches)

    def clear_divergences(self) -> int:
        with self._lock:
            count = len(self._divergences)
            self._divergences.clear()
            return count

    def retained_count(self) -> int:
        """Return the number of currently retained divergences."""
        with self._lock:
            return len(self._divergences)


assert isinstance(DeterministicSemanticInvariantChecker(), SemanticInvariantChecker)
assert isinstance(InMemorySemanticDivergenceDetector(), SemanticDivergenceDetector)
