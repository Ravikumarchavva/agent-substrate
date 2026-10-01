"""Invariant register — a budget caps the whole tree (row I20).

A cap on what an agent may spend is only a cap if spawning more agents cannot get around it. Spend is
therefore counted for the whole execution tree — every agent under the same root, whoever started
which — in the same write as the model call it pays for, and checked before each new call and after
each finished one. Total spend may exceed the cap only by calls already in flight when it was reached.

Stated as a property over generated trees: any number of children, any number of calls each, any cap.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from substrate.kernel.abstractions.agent.supervision import ExecutionBudget, Supervision
from substrate.kernel.abstractions.core.content import ChatMessage, Role, TextBlock
from substrate.kernel.abstractions.core.identity import Actor
from substrate.kernel.abstractions.core.usage import Usage
from substrate.kernel.abstractions.llm import GenerationOptions, ModelCapabilities
from substrate.kernel.abstractions.messaging.message import DataPayload, Message
from substrate.kernel.abstractions.messaging.stream import CompletionEvent
from substrate.kernel.abstractions.runtime.log_entry import RunLogKind
from substrate.kernel.runtime import Runtime


class _CostedLLM:
    model = "costed"
    capabilities = ModelCapabilities(model_id="costed")

    def __init__(self, tokens_per_call: int) -> None:
        self._tokens = tokens_per_call

    async def generate(self, messages: Any, *, options: Any = None, ctx: Any = None) -> Any:
        raise NotImplementedError

    def generate_stream(self, messages: list[ChatMessage], *, options: GenerationOptions = GenerationOptions(), ctx: Any = None) -> AsyncIterator[CompletionEvent]:  # noqa: B008
        return self._stream()

    async def _stream(self) -> AsyncIterator[CompletionEvent]:
        yield CompletionEvent(content=[TextBlock(text="x")], usage=Usage(input_tokens=self._tokens))

    async def count_tokens(self, messages: list[ChatMessage]) -> int:
        return 0


class _Worker:
    """Makes ``calls`` model calls, one after another."""

    def __init__(self, key: str, calls: int, tokens: int) -> None:
        self.id = Actor("agent", key)
        self.model = _CostedLLM(tokens)
        self._calls = calls

    async def run(self, ctx: Any, inbox: list[Message]) -> None:
        for _ in range(self._calls):
            await ctx.llm([ChatMessage(role=Role.USER, content="go")])


class _Boss:
    id = Actor("agent", "boss")

    def __init__(self, workers: list[_Worker], cap: int) -> None:
        self._workers = workers
        self._cap = cap

    async def run(self, ctx: Any, inbox: list[Message]) -> None:
        supervision = Supervision.root(self.id, execution_budget=ExecutionBudget(max_tokens=self._cap))
        handles = [
            await ctx.spawn(w.id, boot=Message(target=w.id, sender=self.id, payload=DataPayload(data={})), supervision=supervision)
            for w in self._workers
        ]
        for handle in handles:
            await ctx.join(handle)


async def _run(children: int, calls: int, tokens: int, cap: int) -> tuple[int, int, int]:
    """(tokens spent by the tree, children that failed, children that finished)."""
    workers = [_Worker(f"w{i}", calls, tokens) for i in range(children)]
    boss = _Boss(workers, cap)
    async with Runtime.local(":memory:") as rt:
        for w in workers:
            await rt.register(w)
        await rt.register(boss)
        run_id = await rt.submit(boss.id, Message(target=boss.id, sender=Actor.system("t"), payload=DataPayload(data={})), max_retries=0)
        async for entry in rt.tail(run_id):
            if entry.kind in (RunLogKind.RUN_COMPLETED, RunLogKind.RUN_FAILED):
                break
        child_runs = [r for w in workers for r in await rt.store.find_runs(agent=w.id, active_only=False)]
        spent = (await rt.store.tree_spend(child_runs[0].run_id)).tokens if child_runs else 0
        failed = sum(1 for r in child_runs if r.status == "failed")
        return spent, failed, len(child_runs) - failed


@settings(max_examples=20, deadline=None)
@given(
    children=st.integers(min_value=1, max_value=4),
    calls=st.integers(min_value=1, max_value=4),
    tokens=st.integers(min_value=10, max_value=60),
    cap=st.integers(min_value=1, max_value=400),
)
def test_i20_total_spend_never_exceeds_the_cap_by_more_than_the_calls_in_flight(children: int, calls: int, tokens: int, cap: int) -> None:
    spent, failed, finished = asyncio.run(asyncio.wait_for(_run(children, calls, tokens, cap), 30))

    # Every child can have one call in flight at the moment the cap is reached.
    assert spent <= cap + children * tokens, (
        f"{children} children x {calls} calls x {tokens} tokens spent {spent} against a cap of {cap}"
    )
    demand = children * calls * tokens
    if demand <= cap:
        assert failed == 0 and spent == demand, "a tree under its budget was stopped, or mis-counted"
    if spent > cap:
        assert failed >= 1, "the cap was passed and nothing stopped"


def test_i20_spawning_more_agents_is_not_a_way_around_a_cap() -> None:
    """The same budget, split across many children, stops the tree as one agent would."""
    spent, failed, _ = asyncio.run(asyncio.wait_for(_run(children=4, calls=4, tokens=50, cap=200), 30))
    assert spent <= 200 + 4 * 50
    assert failed >= 1
