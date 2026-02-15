"""Data models for the evaluation framework.

All models are Pydantic BaseModel for JSON serialization,
validation, and schema generation.
"""
from __future__ import annotations

import statistics
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field


# ---------------------------------------------------------------------------
# Eval Case — a single test case
# ---------------------------------------------------------------------------

class EvalCase(BaseModel):
    """A single evaluation test case.

    Attributes:
        case_id: Unique identifier (auto-generated if not provided).
        input: The user input / prompt to send to the agent.
        expected_output: Ground-truth expected answer (for automated scoring).
        expected_tool_calls: Expected tools the agent should invoke.
        context: Additional context or reference documents.
        tags: Labels for filtering / grouping (e.g. ["math", "easy"]).
        metadata: Arbitrary key-value pairs.
    """
    case_id: str = Field(default_factory=lambda: str(uuid4()))
    input: str
    expected_output: Optional[str] = None
    expected_tool_calls: Optional[List[str]] = None
    context: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": False}


# ---------------------------------------------------------------------------
# Eval Dataset — a collection of test cases
# ---------------------------------------------------------------------------

class EvalDataset(BaseModel):
    """A named collection of evaluation cases.

    Can be loaded from JSON / YAML or constructed programmatically.
    """
    name: str = "default"
    description: str = ""
    cases: List[EvalCase] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @computed_field
    @property
    def size(self) -> int:
        return len(self.cases)

    def filter_by_tag(self, tag: str) -> "EvalDataset":
        """Return a new dataset containing only cases with the given tag."""
        filtered = [c for c in self.cases if tag in c.tags]
        return EvalDataset(
            name=f"{self.name}[tag={tag}]",
            description=self.description,
            cases=filtered,
            metadata=self.metadata,
        )

    @classmethod
    def from_list(
        cls,
        items: List[Dict[str, Any]],
        name: str = "default",
    ) -> "EvalDataset":
        """Build a dataset from a list of dicts."""
        cases = [EvalCase(**item) for item in items]
        return cls(name=name, cases=cases)


# ---------------------------------------------------------------------------
# Eval Score — judgement for a single criterion
# ---------------------------------------------------------------------------

class EvalScore(BaseModel):
    """Score for one criterion on one eval case.

    Attributes:
        criterion: Name of the criterion (e.g. "correctness").
        score: Numeric score 0.0-1.0 (normalised).
        passed: Whether it meets the threshold.
        reasoning: LLM judge's reasoning / explanation.
        raw_score: Original score before normalisation (e.g. 1-5 scale).
    """
    criterion: str
    score: float = Field(ge=0.0, le=1.0)
    passed: bool = True
    reasoning: str = ""
    raw_score: Optional[float] = None
    threshold: float = 0.7

    model_config = {"frozen": False}


# ---------------------------------------------------------------------------
# Eval Case Result — full result for one test case
# ---------------------------------------------------------------------------

class EvalCaseResult(BaseModel):
    """Result of evaluating a single case.

    Links the original case, the agent's actual output, all scores,
    and run metadata (latency, tokens, etc.).
    """
    case_id: str
    input: str
    expected_output: Optional[str] = None
    actual_output: str = ""
    scores: List[EvalScore] = Field(default_factory=list)

    # Agent run metadata
    run_id: str = ""
    status: str = "completed"
    steps_used: int = 0
    tool_calls_total: int = 0
    tool_calls_by_name: Dict[str, int] = Field(default_factory=dict)
    tokens_used: int = 0
    duration_seconds: float = 0.0

    # Eval metadata
    error: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    tags: List[str] = Field(default_factory=list)

    model_config = {"frozen": False}

    @computed_field
    @property
    def passed(self) -> bool:
        """True if all scored criteria passed."""
        if not self.scores:
            return self.error is None
        return all(s.passed for s in self.scores)

    @computed_field
    @property
    def avg_score(self) -> float:
        """Mean score across all criteria."""
        if not self.scores:
            return 0.0
        return statistics.mean(s.score for s in self.scores)

    def score_for(self, criterion: str) -> Optional[EvalScore]:
        """Get score for a specific criterion."""
        for s in self.scores:
            if s.criterion == criterion:
                return s
        return None


# ---------------------------------------------------------------------------
# Eval Report — aggregated results across all cases
# ---------------------------------------------------------------------------

class EvalReport(BaseModel):
    """Aggregated evaluation report.

    Contains per-case results, aggregate metrics, and metadata.
    """
    report_id: str = Field(default_factory=lambda: str(uuid4()))
    dataset_name: str = ""
    agent_name: str = ""
    model: str = ""

    results: List[EvalCaseResult] = Field(default_factory=list)

    # Timing
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    total_duration_seconds: float = 0.0

    # Config snapshot
    criteria_names: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": False}

    # -- Computed aggregates --------------------------------------------------

    @computed_field
    @property
    def total_cases(self) -> int:
        return len(self.results)

    @computed_field
    @property
    def passed_cases(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @computed_field
    @property
    def failed_cases(self) -> int:
        return self.total_cases - self.passed_cases

    @computed_field
    @property
    def error_cases(self) -> int:
        return sum(1 for r in self.results if r.error is not None)

    @computed_field
    @property
    def pass_rate(self) -> float:
        if self.total_cases == 0:
            return 0.0
        return self.passed_cases / self.total_cases

    @computed_field
    @property
    def avg_score(self) -> float:
        scores = [r.avg_score for r in self.results if r.scores]
        return statistics.mean(scores) if scores else 0.0

    @computed_field
    @property
    def avg_latency(self) -> float:
        durations = [r.duration_seconds for r in self.results if r.duration_seconds > 0]
        return statistics.mean(durations) if durations else 0.0

    @computed_field
    @property
    def avg_tokens(self) -> float:
        tokens = [r.tokens_used for r in self.results if r.tokens_used > 0]
        return statistics.mean(tokens) if tokens else 0.0

    @computed_field
    @property
    def total_tokens(self) -> int:
        return sum(r.tokens_used for r in self.results)

    def scores_by_criterion(self) -> Dict[str, Dict[str, float]]:
        """Aggregate scores per criterion.

        Returns:
            Dict mapping criterion name to {"mean", "min", "max", "pass_rate"}.
        """
        from collections import defaultdict

        criterion_scores: Dict[str, List[float]] = defaultdict(list)
        criterion_passed: Dict[str, List[bool]] = defaultdict(list)

        for result in self.results:
            for score in result.scores:
                criterion_scores[score.criterion].append(score.score)
                criterion_passed[score.criterion].append(score.passed)

        aggregated = {}
        for name, scores in criterion_scores.items():
            passed = criterion_passed[name]
            aggregated[name] = {
                "mean": statistics.mean(scores),
                "min": min(scores),
                "max": max(scores),
                "stdev": statistics.stdev(scores) if len(scores) > 1 else 0.0,
                "pass_rate": sum(passed) / len(passed) if passed else 0.0,
            }
        return aggregated

    def filter_failed(self) -> List[EvalCaseResult]:
        """Return only failed cases for debugging."""
        return [r for r in self.results if not r.passed]

    def filter_by_tag(self, tag: str) -> List[EvalCaseResult]:
        """Return cases matching a tag."""
        return [r for r in self.results if tag in r.tags]

    def summary(self) -> str:
        """Human-readable summary string."""
        lines = [
            f"{'='*60}",
            f"  EVAL REPORT: {self.dataset_name}",
            f"  Agent: {self.agent_name} | Model: {self.model}",
            f"{'='*60}",
            f"  Cases:     {self.total_cases}",
            f"  Passed:    {self.passed_cases} ({self.pass_rate:.1%})",
            f"  Failed:    {self.failed_cases}",
            f"  Errors:    {self.error_cases}",
            f"  Avg Score: {self.avg_score:.3f}",
            f"  Avg Latency: {self.avg_latency:.2f}s",
            f"  Total Tokens: {self.total_tokens:,}",
            f"{'-'*60}",
        ]

        by_criterion = self.scores_by_criterion()
        if by_criterion:
            lines.append("  Scores by Criterion:")
            for name, stats in by_criterion.items():
                lines.append(
                    f"    {name:<20s} "
                    f"mean={stats['mean']:.3f}  "
                    f"min={stats['min']:.3f}  "
                    f"max={stats['max']:.3f}  "
                    f"pass={stats['pass_rate']:.1%}"
                )
            lines.append(f"{'='*60}")

        if self.total_duration_seconds > 0:
            lines.append(f"  Total eval time: {self.total_duration_seconds:.1f}s")

        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Full JSON-serializable snapshot."""
        return self.model_dump(mode="json")
