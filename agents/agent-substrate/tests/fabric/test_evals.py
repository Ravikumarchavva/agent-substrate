"""Tests for fabric.evals.EvalRunner."""

from __future__ import annotations

import asyncio
import pytest

from ravi.agents.middleware import AgentRunResult, ToolCallRecord
from ravi.fabric.evals import (
    EvalCase,
    EvalDataset,
    EvalRunner,
    EvalScore,
    CORRECTNESS,
)
from ravi.fabric.evals.judge import LLMJudge


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class OKAgent:
    name = "ok_agent"

    async def run(self, input_text: str, **kwargs) -> AgentRunResult:
        return AgentRunResult(
            output=f"reply to: {input_text}",
            status="success",
            run_id="ok-run",
        )


class SlowAgent:
    name = "slow_agent"

    async def run(self, input_text: str, **kwargs) -> AgentRunResult:
        await asyncio.sleep(10)  # exceeds any reasonable timeout
        return AgentRunResult(output="never", status="success", run_id="slow-run")


class ToolAgent:
    name = "tool_agent"

    async def run(self, input_text: str, **kwargs) -> AgentRunResult:
        calls = [
            ToolCallRecord(
                name="search",
                call_id="c1",
                arguments={},
                result="r",
                is_error=False,
                duration_ms=10,
            ),
            ToolCallRecord(
                name="search",
                call_id="c2",
                arguments={},
                result="r",
                is_error=False,
                duration_ms=10,
            ),
            ToolCallRecord(
                name="calc",
                call_id="c3",
                arguments={},
                result="42",
                is_error=False,
                duration_ms=5,
            ),
        ]
        return AgentRunResult(
            output="done",
            status="success",
            tool_calls=calls,
            run_id="tool-run",
        )


def _dataset(*inputs: str) -> EvalDataset:
    cases = [EvalCase(input=t, expected_output="exp") for t in inputs]
    return EvalDataset(name="test_ds", cases=cases)


# ---------------------------------------------------------------------------
# No-judge tests
# ---------------------------------------------------------------------------

async def test_runner_no_judge_all_success():
    runner = EvalRunner(agent=OKAgent())
    report = await runner.run(_dataset("q1", "q2"))
    assert report.total_cases == 2
    assert report.pass_rate == 1.0
    assert all(r.status == "success" for r in report.results)


async def test_runner_no_judge_scores_empty():
    runner = EvalRunner(agent=OKAgent())
    report = await runner.run(_dataset("q"))
    assert report.results[0].scores == []


async def test_runner_tool_calls_counted():
    runner = EvalRunner(agent=ToolAgent())
    report = await runner.run(_dataset("use tools"))
    result = report.results[0]
    assert result.tool_calls_total == 3
    assert result.tool_calls_by_name["search"] == 2
    assert result.tool_calls_by_name["calc"] == 1


# ---------------------------------------------------------------------------
# Timeout test
# ---------------------------------------------------------------------------

async def test_runner_timeout_marks_error():
    runner = EvalRunner(agent=SlowAgent(), timeout=0.05)
    report = await runner.run(_dataset("slow"))
    result = report.results[0]
    assert result.status == "error"
    assert result.error is not None
    assert result.actual_output == ""


# ---------------------------------------------------------------------------
# Concurrency test
# ---------------------------------------------------------------------------

async def test_runner_concurrency_runs_all():
    runner = EvalRunner(agent=OKAgent(), concurrency=2)
    report = await runner.run(_dataset("a", "b", "c"))
    assert report.total_cases == 3
    assert report.pass_rate == 1.0


# ---------------------------------------------------------------------------
# Judge integration (mock)
# ---------------------------------------------------------------------------

async def test_runner_with_mock_judge_populates_scores(monkeypatch):
    mock_scores = [
        EvalScore(criterion="correctness", score=0.9, passed=True, reasoning="good")
    ]

    async def mock_score(*, input_text, actual_output, expected_output=None, context=None):
        return mock_scores

    judge = LLMJudge.__new__(LLMJudge)
    judge.criteria = [CORRECTNESS]
    monkeypatch.setattr(judge, "score", mock_score)

    runner = EvalRunner(agent=OKAgent(), judge=judge)
    report = await runner.run(_dataset("q"))
    result = report.results[0]
    assert len(result.scores) == 1
    assert result.scores[0].criterion == "correctness"
    assert result.scores[0].score == pytest.approx(0.9)


async def test_runner_judge_skipped_on_error(monkeypatch):
    """Judge must NOT be called when the agent fails."""
    judge_called = []

    async def mock_score(**kw):
        judge_called.append(True)
        return []

    judge = LLMJudge.__new__(LLMJudge)
    judge.criteria = [CORRECTNESS]
    monkeypatch.setattr(judge, "score", mock_score)

    runner = EvalRunner(agent=SlowAgent(), judge=judge, timeout=0.05)
    await runner.run(_dataset("boom"))
    assert judge_called == []


async def test_report_criteria_names_populated():
    mock_scores = [EvalScore(criterion="correctness", score=1.0, passed=True)]

    async def mock_score(**kw):
        return mock_scores

    judge = LLMJudge.__new__(LLMJudge)
    judge.criteria = [CORRECTNESS]

    async def _score(**kw):
        return mock_scores

    judge.score = _score

    runner = EvalRunner(agent=OKAgent(), judge=judge)
    report = await runner.run(_dataset("q"))
    assert "correctness" in report.criteria_names
