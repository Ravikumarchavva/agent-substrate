"""Integration tests for the Stage 0 Runtime.

Covers:
1. Fire-and-forget — agent receives a message, processes it, done.
2. Ask/reply round-trip — agent A asks agent B; B replies; A gets "replied".
3. Social fan-out — producer emits to a topic; followers are woken and receive it.
4. Spawn — parent spawns a child; child receives its boot message.
5. Timeout discrimination — slow agent produces AskOutcome(kind="timed_out"), not "target_failed".
6. Journal at-most-once — write-once record; second record for same effect_id is ignored.
"""

from __future__ import annotations

import asyncio


from substrate.kernel.core.identity import AgentId, TopicId
from substrate.kernel.messaging.message import DataPayload, Message
from substrate.kernel.runtime.communication import AskOutcome
from substrate.kernel.runtime.effects import Effect, EffectResult
from substrate.agents.runtime import Runtime, RunContext
from substrate.agents.runtime.backends import InMemoryJournal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agent_id(name: str) -> AgentId:
    return AgentId(type=name, key="test")


def _msg(target: AgentId | TopicId, data: dict | None = None) -> Message:
    return Message(target=target, payload=DataPayload(data=data or {}))


# ---------------------------------------------------------------------------
# 1. Fire-and-forget delivery
# ---------------------------------------------------------------------------


class RecorderAgent:
    def __init__(self, agent_id: AgentId) -> None:
        self.id = agent_id
        self.received: list[Message] = []
        self.done = asyncio.Event()

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        self.received.extend(inbox)
        self.done.set()


async def test_fire_and_forget_delivery() -> None:
    agent_id = _agent_id("recorder")
    agent = RecorderAgent(agent_id)

    async with Runtime() as rt:
        await rt.register(agent)
        await rt.submit(agent_id, _msg(agent_id, {"hello": "world"}))
        await asyncio.wait_for(agent.done.wait(), timeout=2.0)

    assert len(agent.received) >= 1
    payloads = [
        m.payload.data for m in agent.received if isinstance(m.payload, DataPayload)
    ]  # type: ignore[union-attr]
    assert {"hello": "world"} in payloads


# ---------------------------------------------------------------------------
# 2. Ask / reply round-trip
# ---------------------------------------------------------------------------


class EchoAgent:
    """Replies to every message that has reply_to set."""

    def __init__(self, agent_id: AgentId) -> None:
        self.id = agent_id

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        for msg in inbox:
            if msg.reply_to is not None:
                data = msg.payload.data if isinstance(msg.payload, DataPayload) else {}  # type: ignore[union-attr]
                await ctx.reply(msg, {"echo": data})


class AskerAgent:
    def __init__(self, agent_id: AgentId, target: AgentId) -> None:
        self.id = agent_id
        self.target = target
        self.outcome: AskOutcome | None = None
        self.done = asyncio.Event()

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        msg = _msg(self.target, {"value": 42})
        self.outcome = await ctx.ask(self.target, msg, timeout=3.0)
        self.done.set()


async def test_ask_reply_round_trip() -> None:
    echo_id = _agent_id("echo")
    asker_id = _agent_id("asker")
    echo = EchoAgent(echo_id)
    asker = AskerAgent(asker_id, echo_id)

    async with Runtime() as rt:
        await rt.register(echo)
        await rt.register(asker)
        # Submit asker first — it will ask the echo agent.
        # When ctx.ask delivers to echo's inbox, _on_inbox_deliver auto-spawns an echo run.
        await rt.submit(asker_id, _msg(asker_id, {}))
        await asyncio.wait_for(asker.done.wait(), timeout=5.0)

    assert asker.outcome is not None
    assert asker.outcome.kind == "replied"
    assert asker.outcome.result is not None


# ---------------------------------------------------------------------------
# 3. Social fan-out
# ---------------------------------------------------------------------------


class FanoutListenerAgent:
    def __init__(self, agent_id: AgentId) -> None:
        self.id = agent_id
        self.received: list[Message] = []
        self.fanout_done = asyncio.Event()

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        for msg in inbox:
            self.received.append(msg)
            data = msg.payload.data if isinstance(msg.payload, DataPayload) else {}  # type: ignore[union-attr]
            if data.get("headline"):
                self.fanout_done.set()


async def test_social_fanout() -> None:
    listener1_id = _agent_id("listener1")
    listener2_id = AgentId(type="listener2", key="test")
    listener1 = FanoutListenerAgent(listener1_id)
    listener2 = FanoutListenerAgent(listener2_id)

    async with Runtime() as rt:
        await rt.register(listener1)
        await rt.register(listener2)

        # Both agents follow the topic (no need to boot them first —
        # publish will deliver to inbox, auto-spawning their runs)
        await rt.follow(listener1_id, "news.tech", "feed")
        await rt.follow(listener2_id, "news.tech", "feed")

        topic = TopicId(type="news.tech", source="feed")
        broadcast = _msg(topic, {"headline": "AI breakthrough"})
        await rt.publish("news.tech", "feed", broadcast)

        await asyncio.wait_for(
            asyncio.gather(
                listener1.fanout_done.wait(),
                listener2.fanout_done.wait(),
            ),
            timeout=3.0,
        )

    assert any(
        isinstance(m.payload, DataPayload)
        and m.payload.data.get("headline") == "AI breakthrough"  # type: ignore[union-attr]
        for m in listener1.received
    )
    assert any(
        isinstance(m.payload, DataPayload)
        and m.payload.data.get("headline") == "AI breakthrough"  # type: ignore[union-attr]
        for m in listener2.received
    )


# ---------------------------------------------------------------------------
# 4. Spawn — child receives its boot message
# ---------------------------------------------------------------------------


class ChildAgent:
    def __init__(self, agent_id: AgentId) -> None:
        self.id = agent_id
        self.boot_received: dict | None = None
        self.done = asyncio.Event()

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        for msg in inbox:
            if isinstance(msg.payload, DataPayload):
                self.boot_received = msg.payload.data  # type: ignore[union-attr]
        self.done.set()


class SpawnParentAgent:
    def __init__(self, agent_id: AgentId, child_id: AgentId) -> None:
        self.id = agent_id
        self.child_id = child_id
        self.done = asyncio.Event()

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        boot = _msg(self.child_id, {"task": "compute"})
        await ctx.spawn(self.child_id, boot=boot)
        self.done.set()


async def test_spawn_child_receives_boot() -> None:
    parent_id = _agent_id("spawn_parent")
    child_id = _agent_id("spawn_child")
    child = ChildAgent(child_id)
    parent = SpawnParentAgent(parent_id, child_id)

    async with Runtime() as rt:
        await rt.register(child)
        await rt.register(parent)
        await rt.submit(parent_id, _msg(parent_id, {"start": True}))
        await asyncio.wait_for(parent.done.wait(), timeout=3.0)
        await asyncio.wait_for(child.done.wait(), timeout=3.0)

    assert child.boot_received == {"task": "compute"}


# ---------------------------------------------------------------------------
# 5. Timeout → AskOutcome discrimination
# ---------------------------------------------------------------------------


class SlowAgent:
    """Sleeps indefinitely — never replies."""

    def __init__(self, agent_id: AgentId) -> None:
        self.id = agent_id
        self.started = asyncio.Event()

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        self.started.set()
        await asyncio.sleep(60.0)  # far longer than any test timeout


class TimeoutAskerAgent:
    def __init__(self, agent_id: AgentId, target: AgentId) -> None:
        self.id = agent_id
        self.target = target
        self.outcome: AskOutcome | None = None
        self.done = asyncio.Event()

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        msg = _msg(self.target, {})
        self.outcome = await ctx.ask(self.target, msg, timeout=0.15)
        self.done.set()


async def test_ask_timeout_is_not_target_failed() -> None:
    slow_id = _agent_id("slow_agent")
    asker_id = AgentId(type="timeout_asker", key="test")
    slow = SlowAgent(slow_id)
    asker = TimeoutAskerAgent(asker_id, slow_id)

    async with Runtime() as rt:
        await rt.register(slow)
        await rt.register(asker)
        # Boot the slow agent first so it's RUNNING when asker asks it
        await rt.submit(slow_id, _msg(slow_id, {}))
        await asyncio.wait_for(slow.started.wait(), timeout=2.0)
        # Now submit the asker — it will ask the slow agent and time out
        await rt.submit(asker_id, _msg(asker_id, {}))
        await asyncio.wait_for(asker.done.wait(), timeout=3.0)

    assert asker.outcome is not None
    assert asker.outcome.kind == "timed_out", (
        f"Expected 'timed_out' but got {asker.outcome.kind!r}"
    )


# ---------------------------------------------------------------------------
# 6. Journal at-most-once (write-once semantics)
# ---------------------------------------------------------------------------


async def test_journal_write_once() -> None:
    journal = InMemoryJournal()
    effect_id = Effect.make_id("run-abc", "0", "send_email", {"to": "user@example.com"})

    first = EffectResult(effect_id=effect_id, status="ok", value={"sent": True})
    second = EffectResult(
        effect_id=effect_id, status="ok", value={"sent": False, "duplicate": True}
    )

    await journal.record(first)
    await journal.record(second)  # must be silently ignored

    cached = await journal.lookup(effect_id)
    assert cached is not None
    assert cached.value == {"sent": True}, (
        "Journal must not overwrite an existing result"
    )


async def test_journal_dedup_via_context() -> None:
    """_journaled() does not re-execute fn if the effect_id is already in the journal."""

    class CountingAgent:
        def __init__(self, agent_id: AgentId) -> None:
            self.id = agent_id
            self.call_count = 0
            self.done = asyncio.Event()

        async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
            async def _side_effect() -> dict:
                self.call_count += 1
                return {"n": self.call_count}

            # Snapshot the path stack as run() sees it (Worker's own journaled
            # effects before calling run(), e.g. inbox.drain, may have already
            # consumed earlier indices — this test only cares about relative
            # position, not absolute index 0).
            start_stack = list(ctx._path_stack)

            # First journaled call
            result1 = await ctx._journaled("count", {}, _side_effect)
            # Second call with a DIFFERENT path → different effect_id → executes
            result2 = await ctx._journaled("count", {}, _side_effect)
            # Reset the path stack to simulate replay: same path/effect_id as result1
            ctx._path_stack = start_stack
            result1_replay = await ctx._journaled("count", {}, _side_effect)

            assert result1 == {"n": 1}
            assert result2 == {"n": 2}
            assert result1_replay == {"n": 1}  # replayed from journal, not re-executed
            assert self.call_count == 2  # fn only ran twice, not three times
            self.done.set()

    agent_id = _agent_id("counter")
    agent = CountingAgent(agent_id)

    async with Runtime() as rt:
        await rt.register(agent)
        await rt.submit(agent_id, _msg(agent_id, {}))
        await asyncio.wait_for(agent.done.wait(), timeout=2.0)


async def test_nested_effect_inside_journal_hit_tool_stays_replay_safe() -> None:
    """A tool that journals its own nested effect (e.g. ctx.uuid()) must not
    desync sibling effect ids when the tool call itself becomes a journal hit.

    Regression test for the flat _step_seq bug: with a run-wide flat counter,
    a cache-hit tool call still "consumed" an index for each effect its body
    *would* have journaled internally, but on replay the body never runs, so
    those internal increments never happen — every effect_id after the first
    such hit would diverge and needlessly re-execute (re-billing an LLM call,
    re-sending an email, ...). The hierarchical path fixes this: a tool call
    always consumes exactly one index in its parent scope regardless of hit
    or miss, and its internal effects live in a child scope that is only
    entered when the body genuinely executes.
    """
    from substrate.agents.tools.toolbox import Toolbox
    from substrate.kernel.core.content import TextBlock
    from substrate.kernel.tools import ToolExecutionResult, ToolRisk

    class NestedUuidTool:
        name = "nested_uuid_tool"
        description = "Journals its own uuid() call inside execute()."
        risk = ToolRisk.SAFE
        input_schema: dict = {"type": "object", "properties": {}}
        call_count = 0

        async def execute(self, *, ctx: RunContext | None = None, **_: object):
            NestedUuidTool.call_count += 1
            assert ctx is not None
            request_id = await ctx.uuid()  # nested journaled effect
            return ToolExecutionResult(content=[TextBlock(text=request_id)])

    class NestedEffectAgent:
        def __init__(self, agent_id: AgentId) -> None:
            self.id = agent_id
            self.tools = Toolbox()
            self.tools.add(NestedUuidTool())
            self.done = asyncio.Event()
            self.sibling_ids: list[str] = []

        async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
            # Snapshot the path stack as run() sees it (Worker's own journaled
            # effects before calling run(), e.g. inbox.drain, may have already
            # consumed earlier indices).
            start_stack = list(ctx._path_stack)

            # Live run: tool call (opens a child scope, journals a nested
            # uuid() inside it), then a top-level sibling uuid() call.
            await ctx.tool("nested_uuid_tool")
            sibling_live = await ctx.uuid()
            self.sibling_ids.append(sibling_live)

            # Simulate replay: reset to run()'s own starting path, same
            # journal/run_id — the tool call is now a journal hit (its body
            # must NOT re-execute, so no child scope is entered and
            # NestedUuidTool.call_count must not increase).
            ctx._path_stack = start_stack
            await ctx.tool("nested_uuid_tool")
            sibling_replay = await ctx.uuid()
            self.sibling_ids.append(sibling_replay)

            self.done.set()

    agent_id = _agent_id("nested-effect")
    agent = NestedEffectAgent(agent_id)

    async with Runtime() as rt:
        await rt.register(agent)
        await rt.submit(agent_id, _msg(agent_id, {}))
        await asyncio.wait_for(agent.done.wait(), timeout=2.0)

    assert NestedUuidTool.call_count == 1, (
        "Tool body must execute exactly once — the replayed call is a "
        "journal hit and must not re-run"
    )
    live, replay = agent.sibling_ids
    assert live == replay, (
        "The sibling uuid() call after the tool call must resolve to the "
        "SAME effect_id on replay as it did live — proves the tool call's "
        "hit consumed exactly one index in the parent scope, keeping this "
        "sibling's path aligned"
    )


async def test_supervisor_join() -> None:
    """A parent agent can spawn a child and await its completion via ctx.join()."""
    from substrate.kernel.runtime.ids import RunStatus

    class ChildJoinAgent:
        def __init__(self, agent_id: AgentId) -> None:
            self.id = agent_id

        async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
            # Simply finish
            pass

    class ParentJoinAgent:
        def __init__(self, agent_id: AgentId, child_id: AgentId) -> None:
            self.id = agent_id
            self.child_id = child_id
            self.parent_done = asyncio.Event()
            self.child_result = None

        async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
            boot = _msg(self.child_id, {"task": "do_work"})
            handle = await ctx.spawn(self.child_id, boot=boot)
            self.child_result = await ctx.join(handle)
            self.parent_done.set()

    parent_id = _agent_id("join_parent")
    child_id = _agent_id("join_child")
    child = ChildJoinAgent(child_id)
    parent = ParentJoinAgent(parent_id, child_id)

    async with Runtime() as rt:
        await rt.register(child)
        await rt.register(parent)
        await rt.submit(parent_id, _msg(parent_id, {"start": True}))
        await asyncio.wait_for(parent.parent_done.wait(), timeout=3.0)

    assert parent.child_result is not None
    assert parent.child_result.status == RunStatus.COMPLETED
    assert parent.child_result.run_id != ""


async def test_spawn_inherits_execution_budget_transitively() -> None:
    """ctx.spawn() without an explicit supervision override inherits the
    CALLER's own execution_budget (via Supervision.spawn_child()), not a
    fresh Supervision.root() — and this must hold transitively: a grandchild
    spawned by a child (which was itself spawned with a custom budget) sees
    that same budget too, proving the Worker actually rehydrates
    RunMeta.supervision from Supervisor.supervision_of() at each lease, not
    just at the moment of the original spawn() call."""
    from substrate.kernel.agent.supervision import ExecutionBudget, Supervision

    class GrandchildAgent:
        def __init__(self, agent_id: AgentId) -> None:
            self.id = agent_id
            self.seen_max_tokens: int | None = "unset"  # type: ignore[assignment]
            self.done = asyncio.Event()

        async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
            sup = ctx.meta.supervision
            self.seen_max_tokens = sup.execution_budget.max_tokens if sup else None
            self.done.set()

    class ChildAgent:
        def __init__(self, agent_id: AgentId, grandchild_id: AgentId) -> None:
            self.id = agent_id
            self.grandchild_id = grandchild_id

        async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
            boot = _msg(self.grandchild_id, {})
            # No explicit supervision= override — must inherit from ctx's own.
            await ctx.spawn(self.grandchild_id, boot=boot)

    class RootAgent:
        def __init__(self, agent_id: AgentId, child_id: AgentId) -> None:
            self.id = agent_id
            self.child_id = child_id

        async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
            boot = _msg(self.child_id, {})
            custom_sup = Supervision.root(
                self.child_id, execution_budget=ExecutionBudget(max_tokens=42)
            )
            await ctx.spawn(self.child_id, boot=boot, supervision=custom_sup)

    root_id = _agent_id("budget_root")
    child_id = _agent_id("budget_child")
    grandchild_id = _agent_id("budget_grandchild")
    grandchild = GrandchildAgent(grandchild_id)
    child = ChildAgent(child_id, grandchild_id)
    root = RootAgent(root_id, child_id)

    async with Runtime() as rt:
        await rt.register(grandchild)
        await rt.register(child)
        await rt.register(root)
        await rt.submit(root_id, _msg(root_id, {"start": True}))
        await asyncio.wait_for(grandchild.done.wait(), timeout=3.0)

    assert grandchild.seen_max_tokens == 42


async def test_log_once_does_not_duplicate_across_suspend_resume() -> None:
    """A tool that suspends via SuspendInterrupt (e.g. ask_human) re-executes
    its ENTIRE body on resume — the outer ctx.tool() effect can never be
    recorded before a suspend (SuspendInterrupt is a BaseException
    specifically so it bypasses the `except Exception` that would otherwise
    record it). Anything logged with plain ctx._log() before the suspend
    point would duplicate once per suspend/resume cycle — this is exactly
    the bug reported live: an ask_human question appearing twice in the UI,
    with the SAME request_id, after the human's answer resumed the run.

    ctx.log_once() must append its entry exactly once no matter how many
    times the surrounding tool body re-executes."""
    from substrate.kernel.tools import ToolExecutionResult
    from substrate.kernel.core.content import TextBlock

    log_call_count = 0

    class SuspendingTool:
        name = "suspending_tool"
        description = "suspends once via signal, logs once before doing so"
        input_schema: dict = {"type": "object", "properties": {}}

        async def execute(self, *, ctx=None, **kwargs):
            nonlocal log_call_count
            log_call_count += 1
            request_id = await ctx.uuid()
            await ctx.log_once("input.requested", {"request_id": request_id})
            payload = await ctx.sleep_until_signal(f"hitl:{request_id}")
            return ToolExecutionResult(content=[TextBlock(text=str(payload))])

    class SuspendingAgent:
        def __init__(self, agent_id: AgentId) -> None:
            self.id = agent_id
            self.done = asyncio.Event()

        async def run(self, ctx: RunContext, inbox) -> None:
            await ctx.tool("suspending_tool")
            self.done.set()

    from substrate.agents.tools.toolbox import Toolbox

    agent_id = _agent_id("log_once_suspend")
    agent = SuspendingAgent(agent_id)
    toolbox = Toolbox()
    toolbox.add(SuspendingTool())
    agent.tools = toolbox

    async with Runtime() as rt:
        await rt.register(agent)
        run_id = await rt.submit(agent_id, _msg(agent_id, {}))

        for _ in range(100):
            status = await rt.scheduler.get_status(run_id)
            if status is not None and status.value == "suspended":
                break
            await asyncio.sleep(0.02)
        assert status is not None and status.value == "suspended"

        input_requested_count = 0
        request_id = None
        async for entry in rt.event_log.read(run_id):
            if entry.kind == "input.requested":
                input_requested_count += 1
                request_id = entry.payload["request_id"]
        assert input_requested_count == 1

        await rt.signal_bus.signal(run_id, f"hitl:{request_id}", {"answer": "yes"})
        await asyncio.wait_for(agent.done.wait(), timeout=3.0)

        final_count = 0
        async for entry in rt.event_log.read(run_id):
            if entry.kind == "input.requested":
                final_count += 1
        assert final_count == 1, (
            f"input.requested duplicated across suspend/resume: {final_count} entries"
        )
        assert log_call_count == 2, (
            "sanity check on the premise itself: the tool body should have "
            "genuinely re-executed once (live attempt + one replay-on-resume) "
            f"— got {log_call_count}. If this is 1, the test setup is wrong "
            "and isn't exercising the replay path log_once is meant to guard."
        )
