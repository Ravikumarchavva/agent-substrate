"""Invariant register — replay determinism (rows I5, I6, I14).

Effect identity is derived from a hierarchical path: "the Nth journaled call in
this scope". Everything about at-most-once rests on that path being the same on
a live run and on every replay — if a path shifts, the effect id shifts, the
journal misses, and the run re-executes an effect it already performed. That is
the most expensive failure the system can have, and the hardest to see by
reading.

So it is tested as a property over generated call trees rather than with a
handful of examples: arbitrary nesting, arbitrary concurrent batches, and an
arbitrary subset of calls already journaled. The allocator is driven directly
(not through a whole runtime) so the search space is the algorithm itself and
thousands of trees run in a second.

A *hit* is the interesting case: its body never runs, so none of the journaled
calls it would have made are issued — yet every later sibling must still land
on the path it had live.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from hypothesis import given, settings
from hypothesis import strategies as st

from substrate.runtime import Commit
from substrate.runtime import Journal


class _Allocator(Journal):
    """The path allocator on its own: a journal over an empty record."""

    def __init__(self) -> None:
        super().__init__("run", [], Commit())


@dataclass
class Call:
    """One journaled call in a generated program.

    ``uid`` is the call's logical identity — which call in the source this is —
    assigned by walking the program, so the same call can be compared between a
    live run and a replay.
    """

    uid: int = 0
    children: list["Call"] = field(default_factory=list)
    batch: bool = False  # these children run concurrently


def _number(program: list[Call], counter: list[int] | None = None) -> list[Call]:
    """Assign each call a stable logical id in source order."""
    counter = counter or [0]
    for call in program:
        call.uid = counter[0]
        counter[0] += 1
        _number(call.children, counter)
    return program


async def _execute(
    allocator: _Allocator,
    program: list[Call],
    *,
    hits: frozenset[int],
    observed: dict[int, str],
) -> None:
    """Run a program against the allocator, recording the path of each call.

    Mirrors the real control flow: every call consumes one index in its parent's
    scope whether it hits or misses; only a miss opens a child scope and runs its
    body; a batch forks one stack per sibling.
    """
    index = 0
    while index < len(program):
        call = program[index]
        if call.batch:
            siblings = [program[index]]
            index += 1
            while index < len(program) and program[index].batch:
                siblings.append(program[index])
                index += 1
            stacks = allocator.fork_scopes(len(siblings))
            async def _one(sibling: Call, stack: list[int]) -> None:
                async def _body() -> None:
                    await _run_one(allocator, sibling, hits=hits, observed=observed)
                await allocator.in_scope(stack, _body)
            await asyncio.gather(*(_one(s, st_) for s, st_ in zip(siblings, stacks)))
            continue
        await _run_one(allocator, call, hits=hits, observed=observed)
        index += 1


async def _run_one(
    allocator: _Allocator, call: Call, *, hits: frozenset[int], observed: dict[int, str]
) -> None:
    path = allocator.alloc_path()
    observed[call.uid] = path
    if call.uid in hits:
        return  # journal hit: the body never runs
    if call.children:
        allocator.enter_scope()
        try:
            await _execute(allocator, call.children, hits=hits, observed=observed)
        finally:
            allocator.exit_scope()


# A program: a list of calls, each possibly nesting more calls. ``batch`` marks
# a call as part of a concurrent run with its immediate neighbours.
_calls = st.recursive(
    st.builds(Call, children=st.just([]), batch=st.booleans()),
    lambda inner: st.builds(
        Call, children=st.lists(inner, max_size=3), batch=st.booleans()
    ),
    max_leaves=12,
)
_programs = st.lists(_calls, min_size=1, max_size=6)


def _paths_for(program: list[Call], hits: frozenset[int]) -> dict[int, str]:
    observed: dict[int, str] = {}
    asyncio.run(_execute(_Allocator(), program, hits=hits, observed=observed))
    return observed


@settings(max_examples=300, deadline=None)
@given(program=_programs, hit_seed=st.integers(min_value=0, max_value=2**32))
def test_i06_a_calls_path_is_the_same_on_replay_as_it_was_live(
    program: list[Call], hit_seed: int
) -> None:
    """The core property. Replay a program with an arbitrary set of calls already
    journaled; every call that still runs must land on the path it had live."""
    _number(program)
    live = _paths_for(program, frozenset())

    # Any subset of the calls that ran live may already be journaled.
    uids = sorted(live)
    hits = frozenset(uid for uid in uids if (hit_seed >> (uid % 32)) & 1)
    replayed = _paths_for(program, hits)

    for uid, path in replayed.items():
        assert path == live[uid], (
            f"call {uid} was at path {live[uid]!r} live but {path!r} on replay "
            f"(journal hits: {sorted(hits)}) — its effect id would differ and the "
            f"effect would re-execute"
        )


@settings(max_examples=300, deadline=None)
@given(program=_programs)
def test_i06_no_two_calls_share_a_path(program: list[Call]) -> None:
    """Two distinct calls sharing a path share an effect id: one would be served
    the other's cached result."""
    _number(program)
    observed = _paths_for(program, frozenset())
    collisions = [p for p in set(observed.values()) if list(observed.values()).count(p) > 1]
    assert not collisions, (
        f"distinct calls share paths {collisions}: "
        f"{ {uid: p for uid, p in observed.items() if p in collisions} }"
    )


@settings(max_examples=200, deadline=None)
@given(program=_programs)
def test_i06_the_allocator_returns_to_its_starting_depth(program: list[Call]) -> None:
    """Scope push/pop must balance. An unbalanced stack silently shifts every
    subsequent path in the run."""
    _number(program)
    allocator = _Allocator()
    observed: dict[int, str] = {}
    asyncio.run(_execute(allocator, program, hits=frozenset(), observed=observed))
    assert len(allocator._path_stack) == 1, (
        f"path stack ended at depth {len(allocator._path_stack)}: {allocator._path_stack}"
    )


# ---------------------------------------------------------------------------
# I14 — replay equivalence, through a real runtime
# ---------------------------------------------------------------------------

_OPS = st.sampled_from(["uuid", "now", "random", "tool"])


@settings(max_examples=25, deadline=None)
@given(program=st.lists(_OPS, min_size=1, max_size=6), crash_after=st.integers(min_value=0, max_value=6))
def test_i14_a_replay_makes_the_same_decisions_and_repeats_no_effect(program: list[str], crash_after: int) -> None:
    """Run a program of journaled operations, kill the attempt after an arbitrary prefix, and
    let the retry replay. The retry must see exactly the values the first attempt saw for the
    prefix, and a tool must have run once per call in the program — never again for a call the
    journal already held."""
    from substrate.types import TextBlock
    from substrate.types import Actor
    from substrate.runtime import DataPayload, Message
    from substrate.types import RunLogKind
    from substrate.runtime import RunRetryPolicy
    from substrate.tools import ToolExecutionResult, ToolRisk
    from substrate.runtime import Runtime
    from substrate.tools import Toolbox

    cut = min(crash_after, len(program))
    executed: list[int] = []

    class Counter:
        name = "count"
        description = "counts executions"
        input_schema: dict = {"type": "object", "properties": {"i": {"type": "integer"}}}
        risk = ToolRisk.SAFE
        idempotent = True

        async def execute(self, *, ctx: object = None, i: int = 0, **_: object) -> ToolExecutionResult:
            executed.append(i)
            return ToolExecutionResult(name=self.name, content=[TextBlock(text=f"ran {i}")])

    class Prog:
        def __init__(self) -> None:
            self.id = Actor("agent", "prog")
            self.tools = Toolbox()
            self.tools.add(Counter())
            self.attempts: list[list[object]] = []

        async def run(self, ctx: object, inbox: list[Message]) -> None:
            seen: list[object] = []
            self.attempts.append(seen)
            for i, op in enumerate(program):
                if len(self.attempts) == 1 and i == cut:
                    raise RuntimeError("killed mid-run")
                if op == "uuid":
                    seen.append(await ctx.uuid())  # type: ignore[attr-defined]
                elif op == "now":
                    seen.append((await ctx.now()).isoformat())  # type: ignore[attr-defined]
                elif op == "random":
                    seen.append(await ctx.random())  # type: ignore[attr-defined]
                else:
                    result = await ctx.tool("count", {"i": i})  # type: ignore[attr-defined]
                    seen.append(result.text)

    async def scenario() -> Prog:
        agent = Prog()
        async with Runtime.local(":memory:") as rt:
            await rt.register(agent)
            run_id = await rt.submit(
                agent.id,
                Message(target=agent.id, sender=Actor.system("t"), payload=DataPayload(data={})),
                retry_policy=RunRetryPolicy(max_retries=1, backoff_s=0.0),
            )
            async for entry in rt.tail(run_id):
                if entry.kind in (RunLogKind.RUN_COMPLETED, RunLogKind.RUN_FAILED):
                    assert entry.kind == RunLogKind.RUN_COMPLETED, entry.payload
                    break
        return agent

    agent = asyncio.run(asyncio.wait_for(scenario(), 30))

    if cut >= len(program):
        assert len(agent.attempts) == 1  # nothing killed it
        return
    first, replay = agent.attempts
    assert replay[: len(first)] == first, "the replay saw different values than the first attempt did for the same calls"
    tool_calls = [i for i, op in enumerate(program) if op == "tool"]
    assert sorted(executed) == tool_calls, f"a tool ran {executed} for program {program}: once per call, never again on replay"
