"""Integration tests for the Stage 0 Runtime.

Covers:
1. Fire-and-forget — agent receives a message, processes it, done.
2. Ask/reply round-trip — agent A asks agent B; B replies; A gets "replied".
3. Social fan-out — producer emits to a topic; followers are woken and receive it.
4. Spawn — parent spawns a child; child receives its boot message.
5. Timeout discrimination — slow agent produces AskOutcome(kind="timed_out"), not "target_failed".
6. Effect dedup via RunContext — a journaled effect isn't re-executed on replay.
"""

from __future__ import annotations

import asyncio


from substrate.types import Actor, Topic
from substrate.runtime import DataPayload, Message
from substrate.runtime import AskOutcome
from substrate.runtime import RunRetryPolicy
from substrate.runtime import RunContext
from substrate.testing.runtime import ephemeral_runtime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agent_id(name: str) -> Actor:
    return Actor(type="agent", key=name)


def _msg(target: Actor | Topic, data: dict | None = None) -> Message:
    return Message(
        target=target, sender=Actor.system("test"), payload=DataPayload(data=data or {})
    )


# ---------------------------------------------------------------------------
# 1. Fire-and-forget delivery
# ---------------------------------------------------------------------------


class RecorderAgent:
    def __init__(self, agent_id: Actor) -> None:
        self.id = agent_id
        self.received: list[Message] = []
        self.done = asyncio.Event()

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        self.received.extend(inbox)
        self.done.set()


async def test_fire_and_forget_delivery() -> None:
    agent_id = _agent_id("recorder")
    agent = RecorderAgent(agent_id)

    async with ephemeral_runtime() as rt:
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

    def __init__(self, agent_id: Actor) -> None:
        self.id = agent_id

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        for msg in inbox:
            if msg.reply_to is not None:
                data = msg.payload.data if isinstance(msg.payload, DataPayload) else {}  # type: ignore[union-attr]
                await ctx.reply(msg, {"echo": data})


class AskerAgent:
    def __init__(self, agent_id: Actor, target: Actor) -> None:
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

    async with ephemeral_runtime() as rt:
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
    def __init__(self, agent_id: Actor) -> None:
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
    listener2_id = Actor(type="agent", key="listener2")
    listener1 = FanoutListenerAgent(listener1_id)
    listener2 = FanoutListenerAgent(listener2_id)

    async with ephemeral_runtime() as rt:
        await rt.register(listener1)
        await rt.register(listener2)

        # Both agents follow the topic (no need to boot them first —
        # publish will deliver to inbox, auto-spawning their runs)
        await rt.follow(listener1_id, "news.tech", "feed")
        await rt.follow(listener2_id, "news.tech", "feed")

        topic = Topic("news.tech/feed")
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
    def __init__(self, agent_id: Actor) -> None:
        self.id = agent_id
        self.boot_received: dict | None = None
        self.done = asyncio.Event()

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        for msg in inbox:
            if isinstance(msg.payload, DataPayload):
                self.boot_received = msg.payload.data  # type: ignore[union-attr]
        self.done.set()


class SpawnParentAgent:
    def __init__(self, agent_id: Actor, child_id: Actor) -> None:
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

    async with ephemeral_runtime() as rt:
        await rt.register(child)
        await rt.register(parent)
        await rt.submit(parent_id, _msg(parent_id, {"start": True}))
        await asyncio.wait_for(parent.done.wait(), timeout=3.0)
        await asyncio.wait_for(child.done.wait(), timeout=3.0)

    assert child.boot_received == {"task": "compute"}


# ---------------------------------------------------------------------------
# 4b. spawn_child — derived addresses don't collide across parents
# ---------------------------------------------------------------------------


class RecordingChild:
    """Records the address it was actually spawned at, then replies."""

    def __init__(self, agent_id: Actor) -> None:
        self.id = agent_id

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        for msg in inbox:
            await ctx.reply(msg, {"seen_at": str(self.id)})


class SpawnsAssistantByType:
    """Spawns a same-named ('assistant') child via spawn_child, records
    the address the runtime actually gave it."""

    def __init__(self, agent_id: Actor) -> None:
        self.id = agent_id
        self.child_address: str | None = None
        self.done = asyncio.Event()

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        for msg in inbox:
            boot = _msg(self.id, {"task": "help"})
            handle = await ctx.spawn_child("assistant", boot=boot)
            outcome = await ctx.ask(handle, boot, timeout=3.0)
            output = outcome.result.output  # type: ignore[union-attr]
            self.child_address = (
                output.data["seen_at"] if isinstance(output, DataPayload) else None
            )
            self.done.set()


async def test_spawn_child_derives_distinct_addresses_for_same_type() -> None:
    """The real bug this guards against: two parents both calling
    ctx.spawn_child("assistant", ...) must not land on the same address —
    same type, same locally-allocated path if built naively (both are each
    parent's *first* spawn call), but different run_id per parent is what
    has to keep them apart."""
    data_analyst_id = _agent_id("data_analyst")
    researcher_id = _agent_id("researcher")
    data_analyst = SpawnsAssistantByType(data_analyst_id)
    researcher = SpawnsAssistantByType(researcher_id)

    async with ephemeral_runtime() as rt:
        rt.register_factory("assistant", lambda actor: RecordingChild(actor))
        await rt.register(data_analyst)
        await rt.register(researcher)

        await rt.submit(data_analyst_id, _msg(data_analyst_id, {}))
        await rt.submit(researcher_id, _msg(researcher_id, {}))
        await asyncio.wait_for(data_analyst.done.wait(), timeout=5.0)
        await asyncio.wait_for(researcher.done.wait(), timeout=5.0)

    assert data_analyst.child_address is not None
    assert researcher.child_address is not None
    assert data_analyst.child_address != researcher.child_address, (
        "two parents spawning the same actor type must not collide on one address"
    )
    assert data_analyst.child_address.startswith("assistant/")
    assert researcher.child_address.startswith("assistant/")


# ---------------------------------------------------------------------------
# 5. Timeout → AskOutcome discrimination
# ---------------------------------------------------------------------------


class SlowAgent:
    """Sleeps indefinitely — never replies."""

    def __init__(self, agent_id: Actor) -> None:
        self.id = agent_id
        self.started = asyncio.Event()

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        self.started.set()
        await asyncio.sleep(60.0)  # far longer than any test timeout


class TimeoutAskerAgent:
    def __init__(self, agent_id: Actor, target: Actor) -> None:
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
    asker_id = Actor(type="agent", key="timeout_asker")
    slow = SlowAgent(slow_id)
    asker = TimeoutAskerAgent(asker_id, slow_id)

    async with ephemeral_runtime() as rt:
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
# 6. Effect dedup via RunContext
# ---------------------------------------------------------------------------


async def test_journal_dedup_via_context() -> None:
    """A journaled call is not re-executed when the run replays: the retry gets the
    recorded value back, not a fresh one."""

    class ReplayedAgent:
        def __init__(self, agent_id: Actor) -> None:
            self.id = agent_id
            self.attempts: list[tuple[str, str]] = []

        async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
            self.attempts.append((await ctx.uuid(), await ctx.uuid()))
            if len(self.attempts) == 1:
                raise RuntimeError("transient failure after the effects were journaled")

    agent_id = _agent_id("counter")
    agent = ReplayedAgent(agent_id)

    async with ephemeral_runtime() as rt:
        await rt.register(agent)
        run_id = await rt.submit(
            agent_id,
            _msg(agent_id, {}),
            retry_policy=RunRetryPolicy(max_retries=1, backoff_s=0.0),
        )
        async for entry in rt.tail(run_id):
            if entry.kind in ("run.completed", "run.failed"):
                assert entry.kind == "run.completed"
                break

    first, replay = agent.attempts
    assert first[0] != first[1], (
        "two journaled calls in one run must not share an effect id"
    )
    assert replay == first, (
        "the replay must be served from the journal, not re-executed"
    )


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
    from substrate.tools import Toolbox
    from substrate.types import TextBlock
    from substrate.tools import ToolExecutionResult, ToolRisk

    class NestedUuidTool:
        name = "nested_uuid_tool"
        description = "Journals its own uuid() call inside execute()."
        risk = ToolRisk.SAFE
        idempotent = True
        input_schema: dict = {"type": "object", "properties": {}}
        call_count = 0

        async def execute(self, *, ctx: RunContext | None = None, **_: object):
            NestedUuidTool.call_count += 1
            assert ctx is not None
            request_id = await ctx.uuid()  # nested journaled effect
            return ToolExecutionResult(content=[TextBlock(text=request_id)])

    class NestedEffectAgent:
        def __init__(self, agent_id: Actor) -> None:
            self.id = agent_id
            self.tools = Toolbox()
            self.tools.add(NestedUuidTool())
            self.sibling_ids: list[str] = []

        async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
            # Live attempt: the tool call (a child scope holding a nested uuid()), then a
            # top-level sibling uuid().
            await ctx.tool("nested_uuid_tool")
            self.sibling_ids.append(await ctx.uuid())
            if len(self.sibling_ids) == 1:
                # Fail after journaling, so the retry replays: the tool call is now a journal
                # hit and its body must NOT run again.
                raise RuntimeError("transient failure after the effects were journaled")

    agent_id = _agent_id("nested-effect")
    agent = NestedEffectAgent(agent_id)
    NestedUuidTool.call_count = 0

    async with ephemeral_runtime() as rt:
        await rt.register(agent)
        run_id = await rt.submit(
            agent_id,
            _msg(agent_id, {}),
            retry_policy=RunRetryPolicy(max_retries=1, backoff_s=0.0),
        )
        async for entry in rt.tail(run_id):
            if entry.kind in ("run.completed", "run.failed"):
                assert entry.kind == "run.completed"
                break

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
    from substrate.types import RunStatus

    class ChildJoinAgent:
        def __init__(self, agent_id: Actor) -> None:
            self.id = agent_id

        async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
            # Simply finish
            pass

    class ParentJoinAgent:
        def __init__(self, agent_id: Actor, child_id: Actor) -> None:
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

    async with ephemeral_runtime() as rt:
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
    RunMeta.supervision from SupervisorProtocol.supervision_of() at each lease, not
    just at the moment of the original spawn() call."""
    from substrate.types import ExecutionBudget, Supervision

    class GrandchildAgent:
        def __init__(self, agent_id: Actor) -> None:
            self.id = agent_id
            self.seen_max_tokens: int | None = "unset"  # type: ignore[assignment]
            self.done = asyncio.Event()

        async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
            sup = ctx.meta.supervision
            self.seen_max_tokens = sup.execution_budget.max_tokens if sup else None
            self.done.set()

    class ChildAgent:
        def __init__(self, agent_id: Actor, grandchild_id: Actor) -> None:
            self.id = agent_id
            self.grandchild_id = grandchild_id

        async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
            boot = _msg(self.grandchild_id, {})
            # No explicit supervision= override — must inherit from ctx's own.
            await ctx.spawn(self.grandchild_id, boot=boot)

    class RootAgent:
        def __init__(self, agent_id: Actor, child_id: Actor) -> None:
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

    async with ephemeral_runtime() as rt:
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
    from substrate.tools import ToolExecutionResult, ToolRisk
    from substrate.types import TextBlock

    log_call_count = 0

    class SuspendingTool:
        name = "suspending_tool"
        description = "suspends once via signal, logs once before doing so"
        input_schema: dict = {"type": "object", "properties": {}}
        risk = ToolRisk.SAFE
        idempotent = True

        async def execute(self, *, ctx=None, **kwargs):
            nonlocal log_call_count
            log_call_count += 1
            request_id = await ctx.uuid()
            await ctx.log_once("input.requested", {"request_id": request_id})
            payload = await ctx.sleep_until_signal(f"hitl:{request_id}")
            return ToolExecutionResult(content=[TextBlock(text=str(payload))])

    class SuspendingAgent:
        def __init__(self, agent_id: Actor) -> None:
            self.id = agent_id
            self.done = asyncio.Event()

        async def run(self, ctx: RunContext, inbox) -> None:
            await ctx.tool("suspending_tool")
            self.done.set()

    from substrate.tools import Toolbox

    agent_id = _agent_id("log_once_suspend")
    agent = SuspendingAgent(agent_id)
    toolbox = Toolbox()
    toolbox.add(SuspendingTool())
    agent.tools = toolbox

    async with ephemeral_runtime() as rt:
        await rt.register(agent)
        run_id = await rt.submit(agent_id, _msg(agent_id, {}))

        for _ in range(100):
            status = (await rt.get_run(run_id)).status
            if status == "suspended":
                break
            await asyncio.sleep(0.02)
        assert status == "suspended"

        requests = [e for e in await rt.read(run_id) if e.kind == "input.requested"]
        assert len(requests) == 1
        request_id = requests[0].payload["request_id"]

        await rt.store.signal(run_id, f"hitl:{request_id}", {"answer": "yes"})
        await asyncio.wait_for(agent.done.wait(), timeout=3.0)

        final_count = len(
            [e for e in await rt.read(run_id) if e.kind == "input.requested"]
        )
        assert final_count == 1, (
            f"input.requested duplicated across suspend/resume: {final_count} entries"
        )
        assert log_call_count == 2, (
            "sanity check on the premise itself: the tool body should have "
            "genuinely re-executed once (live attempt + one replay-on-resume) "
            f"— got {log_call_count}. If this is 1, the test setup is wrong "
            "and isn't exercising the replay path log_once is meant to guard."
        )


# ---------------------------------------------------------------------------
# 7. send/emit are journaled — a replay does not deliver twice
# ---------------------------------------------------------------------------


class CountingListener:
    def __init__(self, agent_id: Actor) -> None:
        self.id = agent_id
        self.seen: list[str] = []

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        self.seen.extend(m.id for m in inbox)


class Echo:
    def __init__(self, agent_id: Actor) -> None:
        self.id = agent_id

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        for m in inbox:
            await ctx.reply(m, {"ok": True})


class SendsThenSuspends:
    """Sends and emits, then suspends on an ask: the resume replays the whole body."""

    def __init__(self, agent_id: Actor, peer: Actor, helper: Actor) -> None:
        self.id = agent_id
        self.peer = peer
        self.helper = helper
        self.done = asyncio.Event()

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        await ctx.send(self.peer, _msg(self.peer, {"n": 1}))
        await ctx.emit(Topic("news/feed"), _msg(Topic("news/feed"), {"n": 2}))
        await ctx.ask(self.helper, _msg(self.helper, {}), timeout=3.0)
        self.done.set()


async def test_send_and_emit_deliver_once_when_the_run_replays() -> None:
    sender_id, peer_id = _agent_id("sender"), _agent_id("peer")
    follower_id, helper_id = _agent_id("follower"), _agent_id("helper")
    peer, follower = CountingListener(peer_id), CountingListener(follower_id)
    sender = SendsThenSuspends(sender_id, peer_id, helper_id)

    async with ephemeral_runtime() as rt:
        for a in (peer, follower, Echo(helper_id), sender):
            await rt.register(a)
        await rt.follow(follower_id, "news", "feed")
        await rt.submit(sender_id, _msg(sender_id, {}))
        await asyncio.wait_for(sender.done.wait(), timeout=5.0)
        await asyncio.sleep(0.3)

    assert len(peer.seen) == 1, peer.seen
    assert len(follower.seen) == 1, follower.seen


# ---------------------------------------------------------------------------
# 8. channels from an agent: post once across replays, read what is new
# ---------------------------------------------------------------------------


class PostsThenSuspends:
    def __init__(self, agent_id: Actor, helper: Actor) -> None:
        self.id = agent_id
        self.helper = helper
        self.seen: list[str] = []
        self.done = asyncio.Event()

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        entries = await ctx.read_channel("g")
        self.seen = [e.text for e in entries]
        await ctx.post("g", "on it", read_up_to=entries[-1].seq)
        await ctx.ask(self.helper, _msg(self.helper, {}), timeout=3.0)
        self.done.set()


async def test_a_post_from_a_replaying_run_lands_once_and_a_read_is_stable() -> None:
    from substrate.runtime.channel import Member

    poster_id, helper_id = Actor("agent", "poster@g"), _agent_id("helper2")
    poster = PostsThenSuspends(poster_id, helper_id)
    human = Actor("user", "ravi")

    async with ephemeral_runtime() as rt:
        await rt.register(poster)
        await rt.register(Echo(helper_id))
        store = rt._store  # noqa: SLF001
        await store.channel_open("g", members=[Member(agent=poster_id)])
        await store.channel_append("g", sender=human, text="please do it")
        await asyncio.wait_for(poster.done.wait(), timeout=5.0)
        entries = await store.channel_read("g")

    assert [e.text for e in entries] == ["please do it", "on it"]
    assert poster.seen == ["please do it"]
