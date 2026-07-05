"""Tests for fabric.flows — SequentialFlow, ParallelFlow, ConditionalFlow."""

from __future__ import annotations

import asyncio
import pytest
from dataclasses import dataclass

from substrate.agents.runtime.context import RunContext
from substrate.agents.runtime.runtime import Runtime
from substrate.fabric.flows import ConditionalFlow, ParallelFlow, SequentialFlow
from substrate.kernel.core.content import ChatMessage, Role, TextBlock
from substrate.kernel.core.identity import AgentId
from substrate.kernel.messaging.message import ChatPayload, Message
from substrate.kernel.runtime.ids import new_run_id


# ---------------------------------------------------------------------------
# Helpers — kernel-compliant stub agents
# ---------------------------------------------------------------------------


@dataclass
class EchoAgent:
    """Replies to every message with a fixed text."""

    reply: str
    name: str = "echo"

    @property
    def id(self) -> AgentId:
        return AgentId(type="agent", key=self.name)

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        for msg in inbox:
            await ctx.reply(msg, {"text": self.reply})


async def _run_flow(flow, text: str, *extra_agents, timeout: float = 5.0) -> str:
    """Register flow + extra_agents in a Runtime, submit text, return reply text."""
    sentinel = new_run_id()
    msg = Message(
        target=flow.id,
        payload=ChatPayload(
            message=ChatMessage(role=Role.USER, content=[TextBlock(text=text)])
        ),
        reply_to=sentinel,
    )
    cid = msg.correlation_id
    async with Runtime() as rt:
        for agent in extra_agents:
            await rt.register(agent)
        await rt.register(flow)
        await rt.submit(flow.id, msg)
        # SignalBus is consume-based (matches the durable backend) — poll
        # rather than block.
        deadline = asyncio.get_event_loop().time() + timeout
        payload = None
        while asyncio.get_event_loop().time() < deadline:
            payload = await rt.signal_bus.consume(
                sentinel, f"reply:{cid}", f"test-wait:{sentinel}:{cid}"
            )
            if payload is not None:
                break
            await asyncio.sleep(0.02)
        if payload is None:
            raise asyncio.TimeoutError(
                f"Timed out after {timeout}s waiting for a reply"
            )
    return str(payload.get("text", ""))


# ---------------------------------------------------------------------------
# SequentialFlow
# ---------------------------------------------------------------------------


async def test_sequential_run_accumulates_output():
    a = EchoAgent(name="a", reply="hello")
    b = EchoAgent(name="b", reply="world")
    flow = SequentialFlow(steps=[a, b], name="seq")

    result = await _run_flow(flow, "start", a, b)

    assert "start" in result
    assert "hello" in result
    assert "world" in result


async def test_sequential_run_single_step():
    agent = EchoAgent(name="only", reply="done")
    flow = SequentialFlow(steps=[agent])

    result = await _run_flow(flow, "input", agent)

    assert "done" in result


async def test_sequential_raises_on_empty_steps():
    with pytest.raises(ValueError, match="at least one step"):
        SequentialFlow(steps=[])


async def test_sequential_step_order():
    """Steps run in order; each step receives accumulated output."""
    results: list[str] = []

    @dataclass
    class RecordingAgent:
        name: str
        label: str

        @property
        def id(self) -> AgentId:
            return AgentId(type="agent", key=self.name)

        async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
            for msg in inbox:
                if msg.reply_to:
                    results.append(self.label)
                    await ctx.reply(msg, {"text": self.label})

    a = RecordingAgent(name="ra", label="A")
    b = RecordingAgent(name="rb", label="B")
    c = RecordingAgent(name="rc", label="C")
    flow = SequentialFlow(steps=[a, b, c], name="ordered")

    await _run_flow(flow, "go", a, b, c)

    # Each step only replies once (to the ask message; boot reply is also possible
    # but we filter by reply_to in RecordingAgent)
    assert results == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# ParallelFlow
# ---------------------------------------------------------------------------


async def test_parallel_run_merges_concat():
    a = EchoAgent(name="a", reply="A")
    b = EchoAgent(name="b", reply="B")
    flow = ParallelFlow(branches=[a, b], name="par")

    result = await _run_flow(flow, "in", a, b)

    assert "A" in result
    assert "B" in result


async def test_parallel_raises_on_empty_branches():
    with pytest.raises(ValueError, match="at least one branch"):
        ParallelFlow(branches=[])


async def test_parallel_custom_merge():
    a = EchoAgent(name="a", reply="X")
    b = EchoAgent(name="b", reply="Y")
    flow = ParallelFlow(
        branches=[a, b],
        name="custom_par",
        merge=lambda outputs: " | ".join(outputs),
    )

    result = await _run_flow(flow, "in", a, b)

    assert result == "X | Y"


async def test_parallel_vote_merge():
    a = EchoAgent(name="a", reply="yes")
    b = EchoAgent(name="b", reply="yes")
    c = EchoAgent(name="c", reply="no")
    flow = ParallelFlow(branches=[a, b, c], name="vote_par", merge="vote")

    result = await _run_flow(flow, "in", a, b, c)

    assert result == "yes"


# ---------------------------------------------------------------------------
# ConditionalFlow
# ---------------------------------------------------------------------------


async def test_conditional_routes_true():
    yes = EchoAgent(name="yes", reply="yes_output")
    no = EchoAgent(name="no", reply="no_output")
    flow = ConditionalFlow(
        predicate=lambda t: "yes" in t,
        if_true=yes,
        if_false=no,
    )

    result = await _run_flow(flow, "yes please", yes, no)

    assert result == "yes_output"


async def test_conditional_routes_false():
    yes = EchoAgent(name="yes", reply="yes_output")
    no = EchoAgent(name="no", reply="no_output")
    flow = ConditionalFlow(
        predicate=lambda t: "yes" in t,
        if_true=yes,
        if_false=no,
    )

    result = await _run_flow(flow, "nope", yes, no)

    assert result == "no_output"


async def test_conditional_predicate_exception_takes_if_false():
    yes = EchoAgent(name="yes", reply="yes_output")
    no = EchoAgent(name="no", reply="fallback")
    flow = ConditionalFlow(
        predicate=lambda t: (_ for _ in ()).throw(RuntimeError("boom")),
        if_true=yes,
        if_false=no,
    )

    result = await _run_flow(flow, "anything", yes, no)

    assert result == "fallback"
