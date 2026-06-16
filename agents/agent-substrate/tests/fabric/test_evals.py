"""Tests for fabric.evals.EvalRunner."""

from __future__ import annotations

import asyncio
import pytest
from dataclasses import dataclass

from ravi.agents.runtime.context import RunContext
from ravi.fabric.evals import (
    EvalCase,
    EvalDataset,
    EvalRunner,
    EvalScore,
    CORRECTNESS,
)
from ravi.fabric.evals.judge import LLMJudge
from ravi.kernel.core.identity import AgentId
from ravi.kernel.messaging.message import Message


# ---------------------------------------------------------------------------
# Helpers — kernel-compliant stub agents
# ---------------------------------------------------------------------------


@dataclass
class OKAgent:
    @property
    def id(self) -> AgentId:
        return AgentId(type="agent", key="ok_agent")

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        for msg in inbox:
            # Extract input text from the message payload
            from ravi.kernel.messaging.message import ChatPayload, DataPayload
            from ravi.kernel.core.content import content_blocks_to_str

            p = msg.payload
            if isinstance(p, ChatPayload):
                text = content_blocks_to_str(p.message.content)
            elif isinstance(p, DataPayload):
                text = str(p.data.get("text", ""))
            else:
                text = ""
            await ctx.reply(msg, {"text": f"reply to: {text}"})


@dataclass
class SlowAgent:
    @property
    def id(self) -> AgentId:
        return AgentId(type="agent", key="slow_agent")

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        await asyncio.sleep(10)  # exceeds any reasonable timeout
        for msg in inbox:
            await ctx.reply(msg, {"text": "never"})


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
    assert all(r.status == "completed" for r in report.results)


async def test_runner_no_judge_scores_empty():
    runner = EvalRunner(agent=OKAgent())
    report = await runner.run(_dataset("q"))
    assert report.results[0].scores == []


async def test_runner_output_contains_input():
    runner = EvalRunner(agent=OKAgent())
    report = await runner.run(_dataset("hello"))
    result = report.results[0]
    assert "hello" in result.actual_output


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

    async def mock_score(
        *, input_text, actual_output, expected_output=None, context=None
    ):
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

    async def _score(**kw):
        return mock_scores

    judge = LLMJudge.__new__(LLMJudge)
    judge.criteria = [CORRECTNESS]
    judge.score = _score

    runner = EvalRunner(agent=OKAgent(), judge=judge)
    report = await runner.run(_dataset("q"))
    assert "correctness" in report.criteria_names
