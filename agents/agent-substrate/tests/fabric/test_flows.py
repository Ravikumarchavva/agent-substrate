"""Tests for fabric.flows — SequentialFlow, ParallelFlow, ConditionalFlow."""

from __future__ import annotations

import pytest
from dataclasses import dataclass

from ravi.agents.middleware import AgentRunResult
from ravi.fabric.flows import SequentialFlow, ParallelFlow, ConditionalFlow
from ravi.kernel.messaging.stream import TextDelta


# ---------------------------------------------------------------------------
# Helpers — stub "agent" compatible with BaseFlow duck-typing
# ---------------------------------------------------------------------------

@dataclass
class StubAgent:
    name: str
    reply: str = "ok"
    status: str = "success"

    async def run(self, input_text: str, **kwargs) -> AgentRunResult:
        return AgentRunResult(
            output=self.reply,
            status=self.status,
            run_id="stub-run",
        )

    async def run_stream(self, input_text: str, **kwargs):
        yield TextDelta(text=self.reply)


# ---------------------------------------------------------------------------
# SequentialFlow
# ---------------------------------------------------------------------------

async def test_sequential_run_accumulates_output():
    a = StubAgent(name="a", reply="hello")
    b = StubAgent(name="b", reply="world")
    flow = SequentialFlow(steps=[a, b], name="seq")

    result = await flow.run("start")

    assert result.status == "success"
    # last step's output is returned
    assert result.output == "world"
    assert result.run_id != ""


async def test_sequential_run_single_step():
    agent = StubAgent(name="only", reply="done")
    flow = SequentialFlow(steps=[agent])
    result = await flow.run("input")
    assert result.output == "done"
    assert result.status == "success"


async def test_sequential_raises_on_empty_steps():
    with pytest.raises(ValueError, match="at least one step"):
        SequentialFlow(steps=[])


async def test_sequential_run_stream_yields_text_delta():
    a = StubAgent(name="a", reply="foo")
    b = StubAgent(name="b", reply="bar")
    flow = SequentialFlow(steps=[a, b])

    chunks = []
    async for chunk in flow.run_stream("in"):
        chunks.append(chunk)

    texts = [c.text for c in chunks if isinstance(c, TextDelta)]
    assert "foo" in texts
    assert "bar" in texts


# ---------------------------------------------------------------------------
# ParallelFlow
# ---------------------------------------------------------------------------

async def test_parallel_run_merges_concat():
    a = StubAgent(name="a", reply="A")
    b = StubAgent(name="b", reply="B")
    flow = ParallelFlow(branches=[a, b], name="par")

    result = await flow.run("in")

    assert "A" in result.output
    assert "B" in result.output
    assert result.run_id != ""


async def test_parallel_run_status_success_if_all_success():
    a = StubAgent(name="a", reply="ok")
    b = StubAgent(name="b", reply="ok")
    flow = ParallelFlow(branches=[a, b])
    result = await flow.run("x")
    assert result.status == "success"


async def test_parallel_run_status_error_if_any_fails():
    a = StubAgent(name="a", reply="ok", status="success")
    b = StubAgent(name="b", reply="err", status="error")
    flow = ParallelFlow(branches=[a, b])
    result = await flow.run("x")
    assert result.status == "error"


async def test_parallel_run_stream_yields_from_all_branches():
    a = StubAgent(name="a", reply="X")
    b = StubAgent(name="b", reply="Y")
    flow = ParallelFlow(branches=[a, b])

    chunks = []
    async for chunk in flow.run_stream("in"):
        chunks.append(chunk)

    texts = [c.text for c in chunks if isinstance(c, TextDelta)]
    assert set(texts) == {"X", "Y"}


async def test_parallel_raises_on_empty_branches():
    with pytest.raises(ValueError, match="at least one branch"):
        ParallelFlow(branches=[])


# ---------------------------------------------------------------------------
# ConditionalFlow
# ---------------------------------------------------------------------------

async def test_conditional_routes_true():
    yes = StubAgent(name="yes", reply="yes_output")
    no = StubAgent(name="no", reply="no_output")
    flow = ConditionalFlow(
        predicate=lambda t: "yes" in t,
        if_true=yes,
        if_false=no,
    )
    result = await flow.run("yes please")
    assert result.output == "yes_output"


async def test_conditional_routes_false():
    yes = StubAgent(name="yes", reply="yes_output")
    no = StubAgent(name="no", reply="no_output")
    flow = ConditionalFlow(
        predicate=lambda t: "yes" in t,
        if_true=yes,
        if_false=no,
    )
    result = await flow.run("nope")
    assert result.output == "no_output"


async def test_conditional_predicate_exception_takes_if_false():
    yes = StubAgent(name="yes", reply="yes_output")
    no = StubAgent(name="no", reply="fallback")
    flow = ConditionalFlow(
        predicate=lambda t: (_ for _ in ()).throw(RuntimeError("boom")),
        if_true=yes,
        if_false=no,
    )
    result = await flow.run("anything")
    assert result.output == "fallback"


async def test_conditional_run_stream():
    yes = StubAgent(name="yes", reply="stream_yes")
    no = StubAgent(name="no", reply="stream_no")
    flow = ConditionalFlow(predicate=lambda t: True, if_true=yes, if_false=no)

    chunks = []
    async for chunk in flow.run_stream("in"):
        chunks.append(chunk)
    texts = [c.text for c in chunks if isinstance(c, TextDelta)]
    assert "stream_yes" in texts
