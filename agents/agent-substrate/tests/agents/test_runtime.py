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


from ravi.kernel.core.identity import AgentId, TopicId
from ravi.kernel.messaging.message import DataPayload, Message
from ravi.kernel.runtime.communication import AskOutcome
from ravi.kernel.runtime.effects import Effect, EffectResult
from ravi.agents.runtime import Runtime, RunContext
from ravi.agents.runtime.backends import InMemoryJournal
from ravi.serving.stream.run_adapter import RunStreamAdapter


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


async def test_run_stream_adapter_is_async_iterable() -> None:
    class FakeEventLog:
        async def tail(self, run_id: str):
            yield type("Entry", (), {"kind": "run.completed", "payload": {}})()

        async def read(self, run_id: str):
            if False:
                yield

    class FakeRuntime:
        def __init__(self) -> None:
            self.event_log = FakeEventLog()
            self.submitted = []
            self._registry = {}

        async def submit(self, agent_id, msg):
            self.submitted.append((agent_id, msg))
            return "run-1"

    runtime = FakeRuntime()
    adapter = RunStreamAdapter(
        agent_id=AgentId(type="assistant", key="test-agent"),
        runtime=runtime,
        tools=[],
        correlation_id="corr-1",
    )

    events = []
    async for event in adapter.run_stream("hello"):
        events.append(event)
        break

    assert events


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
    effect_id = Effect.make_id("run-abc", 0, "send_email", {"to": "user@example.com"})

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

            # First journaled call
            result1 = await ctx._journaled("count", {}, _side_effect)
            # Second call with a DIFFERENT step_seq → different effect_id → executes
            result2 = await ctx._journaled("count", {}, _side_effect)
            # Reset seq to 0 to simulate replay: same effect_id as result1
            ctx._step_seq = 0
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


async def test_supervisor_join() -> None:
    """A parent agent can spawn a child and await its completion via ctx.join()."""
    from ravi.kernel.runtime.ids import RunStatus

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
