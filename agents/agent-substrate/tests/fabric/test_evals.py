"""Tests for fabric.evals.EvalRunner."""

from __future__ import annotations

import asyncio
import pytest
from dataclasses import dataclass

from substrate.agents.runtime.context import RunContext
from substrate.fabric.evals import (
    EvalCase,
    EvalDataset,
    EvalRunner,
    EvalScore,
    CORRECTNESS,
    TOOL_USAGE,
)
from substrate.fabric.evals.judge import LLMJudge
from substrate.kernel.core.identity import AgentId
from substrate.kernel.messaging.message import Message


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
            from substrate.kernel.messaging.message import ChatPayload, DataPayload
            from substrate.kernel.core.content import content_blocks_to_str

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


@dataclass
class TracingAgent:
    """Emits a fixed llm.call / tool.call trace, then replies.

    Two LLM steps (100 + 50 tokens) and two calculator tool calls — lets the
    runner's trace aggregation be asserted without a real model.
    """

    @property
    def id(self) -> AgentId:
        return AgentId(type="agent", key="tracing_agent")

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        for msg in inbox:
            await ctx._log("llm.call", {"model": "fake", "tokens": 100})
            await ctx._log("tool.call", {"tool_name": "calculator", "args": {}})
            await ctx._log("tool.result", {"tool_name": "calculator", "ok": True})
            await ctx._log("llm.call", {"model": "fake", "tokens": 50})
            await ctx._log("tool.call", {"tool_name": "calculator", "args": {}})
            await ctx._log("tool.result", {"tool_name": "calculator", "ok": True})
            await ctx.reply(msg, {"text": "done"})


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

    async def mock_score(*, input_text, actual_output, **kwargs):
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


# ---------------------------------------------------------------------------
# Execution-trace capture (tokens / steps / tool calls)
# ---------------------------------------------------------------------------


async def test_runner_captures_trace_from_event_log():
    runner = EvalRunner(agent=TracingAgent())
    report = await runner.run(EvalDataset(name="t", cases=[EvalCase(input="q")]))
    r = report.results[0]
    assert r.run_id != ""
    assert r.steps_used == 2
    assert r.tokens_used == 150
    assert r.tool_calls_total == 2
    assert r.tool_calls_by_name == {"calculator": 2}


async def test_report_aggregates_tokens_across_cases():
    runner = EvalRunner(agent=TracingAgent())
    report = await runner.run(
        EvalDataset(name="t", cases=[EvalCase(input="a"), EvalCase(input="b")])
    )
    assert report.total_tokens == 300
    assert report.avg_tokens == pytest.approx(150.0)


async def test_runner_passes_tool_calls_to_judge(monkeypatch):
    """The runner must feed the actual + expected tool calls to the judge so
    the TOOL_USAGE criterion grades real tool traces, not the reply text."""
    captured: dict[str, object] = {}

    async def mock_score(
        *,
        input_text,
        actual_output,
        expected_output=None,
        context=None,
        expected_tool_calls=None,
        actual_tool_calls=None,
    ):
        captured["expected"] = expected_tool_calls
        captured["actual"] = actual_tool_calls
        return [EvalScore(criterion="tool_usage", score=1.0, passed=True)]

    judge = LLMJudge.__new__(LLMJudge)
    judge.criteria = [TOOL_USAGE]
    monkeypatch.setattr(judge, "score", mock_score)

    runner = EvalRunner(agent=TracingAgent(), judge=judge)
    await runner.run(
        EvalDataset(
            name="t",
            cases=[EvalCase(input="q", expected_tool_calls=["calculator"])],
        )
    )
    assert captured["actual"] == ["calculator", "calculator"]
    assert captured["expected"] == ["calculator"]
