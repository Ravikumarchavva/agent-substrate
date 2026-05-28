"""Tests for in-memory semantic consistency implementations."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from ravi.extensions.semantics import (
    DeterministicSemanticInvariantChecker,
    InMemorySemanticDivergenceDetector,
)
from ravi.kernel.semantics import (
    SemanticDivergenceDetector,
    SemanticInvariant,
    SemanticInvariantChecker,
    SemanticInvariantKind,
    SemanticPayload,
    SemanticSeverity,
)


def _invariant(
    invariant_id: str,
    kind: SemanticInvariantKind,
    **kwargs: object,
) -> SemanticInvariant:
    return SemanticInvariant(
        invariant_id=invariant_id,
        kind=kind,
        **kwargs,
    )


class TestProtocolConformance:
    def test_checker_protocol(self) -> None:
        assert isinstance(
            DeterministicSemanticInvariantChecker(),
            SemanticInvariantChecker,
        )

    def test_detector_protocol(self) -> None:
        assert isinstance(
            InMemorySemanticDivergenceDetector(),
            SemanticDivergenceDetector,
        )


class TestFieldExists:
    def test_passes_for_present_nested_field(self) -> None:
        checker = DeterministicSemanticInvariantChecker()
        invariant = _invariant(
            "has-email",
            SemanticInvariantKind.FIELD_EXISTS,
            field_path="user.contacts.0.email",
        )

        result = checker.evaluate(
            invariant,
            event={"user": {"contacts": [{"email": "a@example.com"}]}},
        )

        assert result.passed
        assert result.actual == "a@example.com"

    def test_fails_for_missing_field(self) -> None:
        checker = DeterministicSemanticInvariantChecker()
        invariant = _invariant(
            "has-email",
            SemanticInvariantKind.FIELD_EXISTS,
            field_path="user.email",
            severity=SemanticSeverity.WARNING,
        )

        result = checker.evaluate(invariant, event={"user": {}})

        assert not result.passed
        assert result.severity is SemanticSeverity.WARNING
        assert "missing" in result.message


class TestFieldEquals:
    def test_passes_when_value_matches(self) -> None:
        checker = DeterministicSemanticInvariantChecker()
        invariant = _invariant(
            "status-open",
            SemanticInvariantKind.FIELD_EQUALS,
            field_path="status",
            expected="open",
        )

        result = checker.evaluate(invariant, event={"status": "open"})

        assert result.passed
        assert result.actual == "open"

    def test_fails_when_value_differs(self) -> None:
        checker = DeterministicSemanticInvariantChecker()
        invariant = _invariant(
            "status-open",
            SemanticInvariantKind.FIELD_EQUALS,
            field_path="status",
            expected="open",
        )

        result = checker.evaluate(invariant, event={"status": "closed"})

        assert not result.passed
        assert result.actual == "closed"
        assert result.expected == "open"

    def test_can_read_state_payload(self) -> None:
        checker = DeterministicSemanticInvariantChecker()
        invariant = _invariant(
            "state-owner",
            SemanticInvariantKind.FIELD_EQUALS,
            field_path="owner",
            target=SemanticPayload.STATE,
            expected="tenant-a",
        )

        result = checker.evaluate(
            invariant,
            state={"owner": "tenant-a"},
            event={"owner": "tenant-b"},
        )

        assert result.passed


class TestNumericRange:
    def test_passes_inside_range(self) -> None:
        checker = DeterministicSemanticInvariantChecker()
        invariant = _invariant(
            "temperature",
            SemanticInvariantKind.NUMERIC_RANGE,
            field_path="temp",
            min_value=0,
            max_value=100,
        )

        result = checker.evaluate(invariant, event={"temp": 72})

        assert result.passed

    def test_fails_outside_range(self) -> None:
        checker = DeterministicSemanticInvariantChecker()
        invariant = _invariant(
            "temperature",
            SemanticInvariantKind.NUMERIC_RANGE,
            field_path="temp",
            min_value=0,
            max_value=100,
        )

        result = checker.evaluate(invariant, event={"temp": 101})

        assert not result.passed
        assert result.actual == 101

    @pytest.mark.parametrize("value", ["7", True, float("nan"), float("inf")])
    def test_fails_for_non_clean_numbers(self, value: object) -> None:
        checker = DeterministicSemanticInvariantChecker()
        invariant = _invariant(
            "score",
            SemanticInvariantKind.NUMERIC_RANGE,
            field_path="score",
            min_value=0,
        )

        result = checker.evaluate(invariant, event={"score": value})

        assert not result.passed
        assert "finite number" in result.message


class TestMonotonicSequence:
    def test_first_value_initializes_then_advances(self) -> None:
        checker = DeterministicSemanticInvariantChecker()
        invariant = _invariant(
            "offset",
            SemanticInvariantKind.MONOTONIC_SEQUENCE,
            field_path="offset",
        )

        first = checker.evaluate(invariant, event={"offset": 1})
        second = checker.evaluate(invariant, event={"offset": 2})

        assert first.passed
        assert second.passed

    def test_equal_value_allowed_by_default(self) -> None:
        checker = DeterministicSemanticInvariantChecker()
        invariant = _invariant(
            "offset",
            SemanticInvariantKind.MONOTONIC_SEQUENCE,
            field_path="offset",
        )

        checker.evaluate(invariant, event={"offset": 2})
        result = checker.evaluate(invariant, event={"offset": 2})

        assert result.passed

    def test_strict_monotonic_rejects_equal_value(self) -> None:
        checker = DeterministicSemanticInvariantChecker()
        invariant = _invariant(
            "offset",
            SemanticInvariantKind.MONOTONIC_SEQUENCE,
            field_path="offset",
            allow_equal=False,
        )

        checker.evaluate(invariant, event={"offset": 2})
        result = checker.evaluate(invariant, event={"offset": 2})

        assert not result.passed
        assert result.expected == 2

    def test_regression_keeps_previous_high_water_mark(self) -> None:
        checker = DeterministicSemanticInvariantChecker()
        invariant = _invariant(
            "offset",
            SemanticInvariantKind.MONOTONIC_SEQUENCE,
            field_path="offset",
        )

        checker.evaluate(invariant, event={"offset": 10})
        regression = checker.evaluate(invariant, event={"offset": 8})
        still_regressed = checker.evaluate(invariant, event={"offset": 9})

        assert not regression.passed
        assert not still_regressed.passed
        assert still_regressed.expected == 10

    def test_subject_path_isolated(self) -> None:
        checker = DeterministicSemanticInvariantChecker()
        invariant = _invariant(
            "offset",
            SemanticInvariantKind.MONOTONIC_SEQUENCE,
            field_path="offset",
            subject_path="stream",
        )

        checker.evaluate(invariant, event={"stream": "a", "offset": 10})
        result = checker.evaluate(invariant, event={"stream": "b", "offset": 1})

        assert result.passed
        assert result.details["subject_id"] == "b"


class TestCustomPredicate:
    def test_passes_when_predicate_true(self) -> None:
        checker = DeterministicSemanticInvariantChecker()
        invariant = _invariant(
            "state-event-tenant",
            SemanticInvariantKind.CUSTOM_PREDICATE,
            predicate=lambda state, event: state["tenant"] == event["tenant"],
        )

        result = checker.evaluate(
            invariant,
            state={"tenant": "t1"},
            event={"tenant": "t1"},
        )

        assert result.passed

    def test_fails_when_predicate_false(self) -> None:
        checker = DeterministicSemanticInvariantChecker()
        invariant = _invariant(
            "state-event-tenant",
            SemanticInvariantKind.CUSTOM_PREDICATE,
            predicate=lambda state, event: state["tenant"] == event["tenant"],
        )

        result = checker.evaluate(
            invariant,
            state={"tenant": "t1"},
            event={"tenant": "t2"},
        )

        assert not result.passed

    def test_predicate_exception_becomes_failed_result(self) -> None:
        checker = DeterministicSemanticInvariantChecker()
        invariant = _invariant(
            "bad-custom",
            SemanticInvariantKind.CUSTOM_PREDICATE,
            predicate=lambda state, event: event["missing"] == state["missing"],
        )

        result = checker.evaluate(invariant, state={}, event={})

        assert not result.passed
        assert result.details["error_type"] == "KeyError"


class TestDivergenceDetection:
    def test_detect_returns_only_failures(self) -> None:
        detector = InMemorySemanticDivergenceDetector()
        invariants = [
            _invariant(
                "has-id",
                SemanticInvariantKind.FIELD_EXISTS,
                field_path="id",
            ),
            _invariant(
                "status-open",
                SemanticInvariantKind.FIELD_EQUALS,
                field_path="status",
                expected="open",
                severity=SemanticSeverity.CRITICAL,
            ),
        ]

        divergences = detector.detect(
            invariants,
            event={"id": "1", "status": "closed"},
            subject_id="order-1",
        )

        assert len(divergences) == 1
        assert divergences[0].invariant_id == "status-open"
        assert divergences[0].severity is SemanticSeverity.CRITICAL
        assert divergences[0].subject_id == "order-1"
        assert detector.retained_count() == 1

    def test_query_filters_by_severity_invariant_subject_and_limit(self) -> None:
        detector = InMemorySemanticDivergenceDetector()
        warning = _invariant(
            "has-id",
            SemanticInvariantKind.FIELD_EXISTS,
            field_path="id",
            severity=SemanticSeverity.WARNING,
        )
        critical = _invariant(
            "has-total",
            SemanticInvariantKind.FIELD_EXISTS,
            field_path="total",
            severity=SemanticSeverity.CRITICAL,
        )

        detector.detect([warning], event={}, subject_id="a")
        detector.detect([critical], event={}, subject_id="b")
        detector.detect([critical], event={}, subject_id="c")

        recent = detector.recent_divergences(limit=2)
        severe = detector.recent_divergences(
            invariant_id="has-total",
            severity_at_least=SemanticSeverity.ERROR,
        )
        subject = detector.recent_divergences(subject_id="b")

        assert [d.subject_id for d in recent] == ["c", "b"]
        assert [d.subject_id for d in severe] == ["c", "b"]
        assert len(subject) == 1
        assert subject[0].subject_id == "b"

    def test_retention_evicts_oldest(self) -> None:
        detector = InMemorySemanticDivergenceDetector(retention_capacity=2)
        invariant = _invariant(
            "has-id",
            SemanticInvariantKind.FIELD_EXISTS,
            field_path="id",
        )

        detector.detect([invariant], event={}, subject_id="first")
        detector.detect([invariant], event={}, subject_id="second")
        detector.detect([invariant], event={}, subject_id="third")

        assert [d.subject_id for d in detector.recent_divergences()] == [
            "third",
            "second",
        ]

    def test_clear_divergences_returns_count(self) -> None:
        detector = InMemorySemanticDivergenceDetector()
        invariant = _invariant(
            "has-id",
            SemanticInvariantKind.FIELD_EXISTS,
            field_path="id",
        )
        detector.detect([invariant], event={}, subject_id="a")

        assert detector.clear_divergences() == 1
        assert detector.recent_divergences() == ()

    def test_negative_limit_rejected(self) -> None:
        detector = InMemorySemanticDivergenceDetector()
        with pytest.raises(ValueError, match="limit"):
            detector.recent_divergences(limit=-1)

    def test_invalid_retention_rejected(self) -> None:
        with pytest.raises(ValueError, match="retention_capacity"):
            InMemorySemanticDivergenceDetector(retention_capacity=0)


class TestConcurrency:
    def test_concurrent_detection_retains_all_divergences(self) -> None:
        detector = InMemorySemanticDivergenceDetector(retention_capacity=200)
        invariant = _invariant(
            "has-id",
            SemanticInvariantKind.FIELD_EXISTS,
            field_path="id",
        )

        def check(index: int) -> int:
            return len(detector.detect([invariant], event={}, subject_id=str(index)))

        with ThreadPoolExecutor(max_workers=8) as pool:
            counts = list(pool.map(check, range(100)))

        assert sum(counts) == 100
        assert len(detector.recent_divergences()) == 100
