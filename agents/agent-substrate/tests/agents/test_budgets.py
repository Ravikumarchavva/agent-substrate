"""Tests for budget wiring: ExecutionTracker in ReActAgent, SpawnTracker in OrchestratorAgent."""

from __future__ import annotations

import pytest

from substrate.agents.resources.budget import ExecutionTracker
from substrate.agents.supervision.budget import SpawnTracker
from substrate.kernel.agent.supervision import Priority, SpawnBudget
from substrate.kernel.core.errors import BudgetExhaustedError
from substrate.kernel.core.identity import AgentId


# ---------------------------------------------------------------------------
# ExecutionTracker unit tests
# ---------------------------------------------------------------------------


def test_execution_tracker_consumes_tokens() -> None:
    tracker = ExecutionTracker(max_tokens=100)
    tracker.consume(tokens=40)
    tracker.consume(tokens=50)
    assert tracker.used_tokens == 90


def test_execution_tracker_raises_on_token_overflow() -> None:
    tracker = ExecutionTracker(max_tokens=50)
    tracker.consume(tokens=30)
    with pytest.raises(BudgetExhaustedError, match="Token budget exceeded"):
        tracker.consume(tokens=30)


def test_execution_tracker_raises_on_turn_overflow() -> None:
    tracker = ExecutionTracker(max_turns=3)
    tracker.consume(turns=1)
    tracker.consume(turns=1)
    tracker.consume(turns=1)
    with pytest.raises(BudgetExhaustedError, match="Turn limit exceeded"):
        tracker.consume(turns=1)


def test_execution_tracker_raises_on_cost_overflow() -> None:
    tracker = ExecutionTracker(max_cost_usd=0.01)
    with pytest.raises(BudgetExhaustedError, match="Cost budget exceeded"):
        tracker.consume(cost=0.02)


def test_execution_tracker_unlimited_by_default() -> None:
    tracker = ExecutionTracker()
    for _ in range(1000):
        tracker.consume(tokens=1000, turns=1, cost=1.0)
    assert tracker.used_tokens == 1_000_000


# ---------------------------------------------------------------------------
# SpawnTracker unit tests
# ---------------------------------------------------------------------------


def _agent(key: str) -> AgentId:
    return AgentId(type="agent", key=key)


def test_spawn_tracker_acquires_and_releases() -> None:
    tracker = SpawnTracker(SpawnBudget(max_agents=3))
    a, b = _agent("a"), _agent("b")
    tracker.acquire(a)
    tracker.acquire(b)
    assert tracker.total_spawned == 3  # root + 2
    tracker.release(a)
    assert tracker.total_spawned == 2
    tracker.release(b)
    assert tracker.total_spawned == 1


def test_spawn_tracker_blocks_at_cap() -> None:
    tracker = SpawnTracker(SpawnBudget(max_agents=2))
    tracker.acquire(_agent("a"))  # root + a = 2, at cap
    with pytest.raises(BudgetExhaustedError, match="headcount cap"):
        tracker.acquire(_agent("b"), priority=Priority.NORMAL)


def test_spawn_tracker_high_priority_preempts() -> None:
    tracker = SpawnTracker(SpawnBudget(max_agents=2))
    low = _agent("low")
    high = _agent("high")
    tracker.acquire(low, priority=Priority.LOW)  # now at cap
    tracker.acquire(high, priority=Priority.HIGH)  # preempts low
    assert tracker.is_paused(low)
    assert not tracker.is_paused(high)


def test_spawn_tracker_cannot_preempt_equal_priority() -> None:
    tracker = SpawnTracker(SpawnBudget(max_agents=2))
    tracker.acquire(_agent("a"), priority=Priority.HIGH)  # at cap
    with pytest.raises(BudgetExhaustedError, match="Cannot preempt"):
        tracker.acquire(_agent("b"), priority=Priority.HIGH)


def test_spawn_tracker_reprioritize_lifts_pause() -> None:
    tracker = SpawnTracker(SpawnBudget(max_agents=2))
    low = _agent("low")
    high = _agent("high")
    tracker.acquire(low, priority=Priority.LOW)
    tracker.acquire(high, priority=Priority.HIGH)
    assert tracker.is_paused(low)
    tracker.reprioritize(low, Priority.HIGH)
    assert not tracker.is_paused(low)


# ---------------------------------------------------------------------------
# ReActAgent + ExecutionTracker integration (mocked LLM)
# ---------------------------------------------------------------------------


async def test_react_agent_respects_execution_budget() -> None:
    from substrate.agents.core.react import ReActAgent
    from substrate.agents.runtime import Runtime
    from substrate.kernel.core.content import ChatMessage, Role, TextBlock
    from substrate.kernel.core.identity import AgentId
    from substrate.kernel.core.usage import Usage
    from substrate.kernel.messaging.message import Message, ChatPayload
    from substrate.kernel.messaging.stream import CompletionEvent

    class MockLLMClient:
        model = "mock-model"

        async def generate_stream(
            self,
            messages: list[ChatMessage],
            *,
            options: object = None,
            ctx: object = None,
        ):  # type: ignore[override]
            yield CompletionEvent(
                content=[TextBlock(text="hello")],
                usage=Usage(input_tokens=60, output_tokens=60),
            )

    llm = MockLLMClient()

    tracker = ExecutionTracker(max_tokens=100)
    agent = ReActAgent(
        "BudgetBot",
        model=llm,
        execution_budget=tracker,
        max_iterations=5,
    )

    async with Runtime() as rt:
        await rt.register(agent)

        msg = Message(
            target=agent.id,
            sender=AgentId(type="proxy", key="user"),
            payload=ChatPayload(
                message=ChatMessage(role=Role.USER, content=[TextBlock(text="hi")])
            ),
        )
        run_id = await rt.submit(agent.id, msg)
        async for entry in rt.event_log.tail(run_id):
            if entry.kind in ("run.completed", "run.failed", "run.cancelled"):
                assert entry.kind == "run.failed"
                assert "Token budget exceeded" in (entry.payload or {}).get("error", "")
                break


async def test_spawned_child_inherits_execution_budget_from_supervision() -> None:
    """A child ctx.spawn()'d under a tight Supervision.execution_budget must
    have it enforced even though the child's own ReActAgent was constructed
    with NO execution_budget at all — this is the gap ExecutionBudget's
    propagation-without-enforcement left open (see kernel/agent/supervision.py
    and docs/claude_docs/kernel's audit): Supervision.execution_budget flowed
    through ctx.spawn()/spawn_child() but nothing ever converted it into an
    enforced ExecutionTracker for the spawned run."""
    from substrate.agents.core.react import ReActAgent
    from substrate.agents.runtime import Runtime
    from substrate.kernel.agent.supervision import ExecutionBudget, Supervision
    from substrate.kernel.core.content import ChatMessage, Role, TextBlock
    from substrate.kernel.core.identity import AgentId
    from substrate.kernel.core.usage import Usage
    from substrate.kernel.messaging.message import ChatPayload, DataPayload, Message
    from substrate.kernel.messaging.stream import CompletionEvent

    class MockLLMClient:
        model = "mock-model"

        async def generate_stream(
            self,
            messages: list[ChatMessage],
            *,
            options: object = None,
            ctx: object = None,
        ):  # type: ignore[override]
            yield CompletionEvent(
                content=[TextBlock(text="hello")],
                usage=Usage(input_tokens=60, output_tokens=60),
            )

    llm = MockLLMClient()
    child_id = AgentId(type="agent", key="budget-child")
    parent_id = AgentId(type="agent", key="budget-parent")

    # No execution_budget passed here — proves the enforcement comes from
    # the inherited Supervision, not a constructor default.
    child = ReActAgent("ChildBot", model=llm, max_iterations=5)
    child.id = child_id

    class SpawningParent:
        id = parent_id

        async def run(self, ctx: object, inbox: list[Message]) -> None:
            boot = Message(
                target=child_id,
                payload=DataPayload(data={}),
            )
            tight_supervision = Supervision.root(
                child_id, execution_budget=ExecutionBudget(max_tokens=100)
            )
            await ctx.spawn(  # type: ignore[attr-defined]
                child_id, boot=boot, supervision=tight_supervision
            )

    parent = SpawningParent()

    async with Runtime() as rt:
        await rt.register(parent)
        await rt.register(child)

        msg = Message(
            target=parent_id,
            sender=AgentId(type="proxy", key="user"),
            payload=ChatPayload(
                message=ChatMessage(role=Role.USER, content=[TextBlock(text="hi")])
            ),
        )
        await rt.submit(parent_id, msg)

        found_child_run = False
        async for run_id in _poll_for_child_run(rt, child_id):
            found_child_run = True
            async for entry in rt.event_log.tail(run_id):
                if entry.kind in ("run.completed", "run.failed", "run.cancelled"):
                    assert entry.kind == "run.failed"
                    assert "Token budget exceeded" in (entry.payload or {}).get(
                        "error", ""
                    )
                    return
            break
        assert found_child_run, "child run was never spawned"


async def _poll_for_child_run(rt, child_id):
    """Yield once the child agent's run_id is discoverable, then stop."""
    import asyncio

    for _ in range(100):
        found = await rt.scheduler.find_run_for_agent(child_id)
        if found is not None:
            yield found[0]
            return
        await asyncio.sleep(0.02)
