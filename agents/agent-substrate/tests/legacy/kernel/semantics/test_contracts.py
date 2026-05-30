"""Tests for semantic consistency kernel contracts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import pytest

from ravi.guardrails.semantic import (
    InvariantEvaluationResult,
    SemanticDivergence,
    SemanticDivergenceDetector,
    SemanticInvariant,
    SemanticInvariantChecker,
    SemanticInvariantKind,
    SemanticPayload,
    SemanticSeverity,
)


class _Checker:
    def evaluate(
        self,
        invariant: SemanticInvariant,
        state: Mapping[str, Any] | None = None,
        event: Mapping[str, Any] | None = None,
        *,
        subject_id: str | None = None,
    ) -> InvariantEvaluationResult:
        return InvariantEvaluationResult(
            invariant_id=invariant.invariant_id,
            passed=True,
            severity=invariant.severity,
        )

    def evaluate_many(
        self,
        invariants: Iterable[SemanticInvariant],
        state: Mapping[str, Any] | None = None,
        event: Mapping[str, Any] | None = None,
        *,
        subject_id: str | None = None,
    ) -> tuple[InvariantEvaluationResult, ...]:
        return tuple(self.evaluate(invariant, state, event) for invariant in invariants)


class _Detector:
    def detect(
        self,
        invariants: Iterable[SemanticInvariant],
        state: Mapping[str, Any] | None = None,
        event: Mapping[str, Any] | None = None,
        *,
        subject_id: str | None = None,
    ) -> tuple[SemanticDivergence, ...]:
        return ()

    def recent_divergences(
        self,
        *,
        invariant_id: str | None = None,
        severity_at_least: SemanticSeverity | None = None,
        subject_id: str | None = None,
        limit: int | None = None,
    ) -> tuple[SemanticDivergence, ...]:
        return ()

    def clear_divergences(self) -> int:
        return 0


class TestProtocolConformance:
    def test_checker_protocol(self) -> None:
        assert isinstance(_Checker(), SemanticInvariantChecker)

    def test_detector_protocol(self) -> None:
        assert isinstance(_Detector(), SemanticDivergenceDetector)


class TestInvariantValidation:
    def test_requires_invariant_id(self) -> None:
        with pytest.raises(ValueError, match="invariant_id"):
            SemanticInvariant(
                invariant_id="",
                kind=SemanticInvariantKind.FIELD_EXISTS,
                field_path="id",
            )

    def test_field_kind_requires_path(self) -> None:
        with pytest.raises(ValueError, match="field_path"):
            SemanticInvariant(
                invariant_id="missing-path",
                kind=SemanticInvariantKind.FIELD_EXISTS,
            )

    def test_numeric_range_requires_bound(self) -> None:
        with pytest.raises(ValueError, match="min_value or max_value"):
            SemanticInvariant(
                invariant_id="empty-range",
                kind=SemanticInvariantKind.NUMERIC_RANGE,
                field_path="score",
            )

    def test_numeric_range_rejects_inverted_bounds(self) -> None:
        with pytest.raises(ValueError, match="min_value cannot exceed"):
            SemanticInvariant(
                invariant_id="bad-range",
                kind=SemanticInvariantKind.NUMERIC_RANGE,
                field_path="score",
                min_value=10,
                max_value=1,
            )

    def test_custom_predicate_requires_callable(self) -> None:
        with pytest.raises(ValueError, match="predicate"):
            SemanticInvariant(
                invariant_id="custom",
                kind=SemanticInvariantKind.CUSTOM_PREDICATE,
            )

    def test_metadata_is_copied(self) -> None:
        metadata = {"owner": "semantics"}
        invariant = SemanticInvariant(
            invariant_id="exists",
            kind=SemanticInvariantKind.FIELD_EXISTS,
            field_path="id",
            metadata=metadata,
        )
        metadata["owner"] = "changed"
        assert invariant.metadata["owner"] == "semantics"


class TestSeverity:
    def test_ordering(self) -> None:
        assert SemanticSeverity.CRITICAL.at_least(SemanticSeverity.ERROR)
        assert not SemanticSeverity.WARNING.at_least(SemanticSeverity.ERROR)


class TestResultAndDivergenceValidation:
    def test_result_requires_invariant_id(self) -> None:
        with pytest.raises(ValueError, match="invariant_id"):
            InvariantEvaluationResult(invariant_id="", passed=True)

    def test_result_details_are_copied(self) -> None:
        details = {"field": "id"}
        result = InvariantEvaluationResult(
            invariant_id="exists",
            passed=False,
            details=details,
        )
        details["field"] = "other"
        assert result.details["field"] == "id"

    def test_divergence_requires_message(self) -> None:
        with pytest.raises(ValueError, match="message"):
            SemanticDivergence(
                invariant_id="exists",
                severity=SemanticSeverity.ERROR,
                message="",
            )

    def test_divergence_result_must_match_invariant(self) -> None:
        result = InvariantEvaluationResult(
            invariant_id="other",
            passed=False,
        )
        with pytest.raises(ValueError, match="mismatch"):
            SemanticDivergence(
                invariant_id="exists",
                severity=SemanticSeverity.ERROR,
                message="failed",
                result=result,
            )

    def test_invariant_can_target_state(self) -> None:
        invariant = SemanticInvariant(
            invariant_id="state-owner",
            kind=SemanticInvariantKind.FIELD_EQUALS,
            field_path="owner",
            target=SemanticPayload.STATE,
            expected="tenant-a",
        )
        assert invariant.target is SemanticPayload.STATE
