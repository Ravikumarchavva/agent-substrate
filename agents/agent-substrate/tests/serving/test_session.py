"""Kernel-native AgentStreamSession tests.

Drives the session with stub kernel agents and a real in-process Runtime.
No mocks — the session, Runtime, and EventLogProtocol all run as they would in prod.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from substrate.agents.runtime.context import RunContext
from substrate.agents.runtime.runtime import Runtime
from substrate.kernel.core.content import ChatMessage, Role, TextBlock
from substrate.kernel.core.identity import AgentId
from substrate.kernel.messaging.message import ChatPayload, Message
from substrate.serving.monolith.sse.bridge import BRIDGE_DONE
from substrate.serving.protocol import (
    HelloEvent,
    RunCompletedEvent,
    RunFailedEvent,
    WireEvent,
    RunCancelledEvent,
)
from substrate.serving.stream.session import AgentStreamSession, tail_wire_events


# ---------------------------------------------------------------------------
# Stub bridge — signals done immediately and never emits HITL events
# ---------------------------------------------------------------------------


class _StubBridge:
    """Mirrors WebHITLBridge: get_event() blocks until signal_done() is called.

    The real bridge's outgoing queue only yields BRIDGE_DONE after the agent's
    finally block (or a cancel) calls signal_done(); a stub that returns DONE
    eagerly would race _WORKERS_DONE ahead of the session's cancel check.
    """

    def __init__(self) -> None:
        self._done = asyncio.Event()

    async def get_event(self) -> Any:
        await self._done.wait()
        return BRIDGE_DONE

    async def signal_done(self) -> None:
        self._done.set()

    def cancel_all_pending(self, reason: str = "") -> int:
        return 0


# ---------------------------------------------------------------------------
# Stub agents
# ---------------------------------------------------------------------------


@dataclass
class ReplyAgent:
    """Replies to every message with a fixed text string.

    Logs a ``text.delta`` entry mirroring what ``ctx.llm()`` does in a real
    ``ReActAgent`` run.
    """

    reply: str
    name: str = "reply"

    @property
    def id(self) -> AgentId:
        return AgentId(type="agent", key=self.name)

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        for msg in inbox:
            await ctx._log("text.delta", {"text": self.reply})
            await ctx.reply(msg, {"text": self.reply})


@dataclass
class CrashAgent:
    """Always raises an exception."""

    name: str = "crash"

    @property
    def id(self) -> AgentId:
        return AgentId(type="agent", key=self.name)

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        raise RuntimeError("intentional crash")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_msg(agent_id: AgentId, text: str = "hello") -> Message:
    return Message(
        target=agent_id,
        payload=ChatPayload(
            message=ChatMessage(role=Role.USER, content=[TextBlock(text=text)])
        ),
    )


async def _stream_events(
    agent: Any, text: str = "hello", timeout: float = 5.0
) -> list[WireEvent]:
    async with Runtime() as rt:
        msg = _make_msg(agent.id, text)
        session = AgentStreamSession(
            runtime=rt,
            agent=agent,
            msg=msg,
            bridge=_StubBridge(),
        )
        return await asyncio.wait_for(_collect(session), timeout=timeout)


async def _collect(session: AgentStreamSession) -> list[WireEvent]:
    return [ev async for ev in session.events()]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_hello_emitted_first() -> None:
    agent = ReplyAgent(reply="hi", name="hi_agent")
    events = await _stream_events(agent)
    assert isinstance(events[0], HelloEvent)


async def test_terminal_completed_on_clean_run() -> None:
    agent = ReplyAgent(reply="done", name="done_agent")
    events = await _stream_events(agent)
    assert isinstance(events[-1], RunCompletedEvent)


async def test_error_emits_run_failed() -> None:
    agent = CrashAgent(name="crash_agent")
    events = await _stream_events(agent)
    assert any(isinstance(e, RunFailedEvent) for e in events)
    failed = next(e for e in events if isinstance(e, RunFailedEvent))
    assert "crash" in (failed.error or "").lower() or failed.error


async def test_run_survives_disconnect_through_suspend_and_resume() -> None:
    """The scenario that used to cause data loss: a run suspends on
    ask_human, the browser disconnects (a refresh does this), the human
    answers some time LATER, the run resumes and only then produces its
    final response — which must still land in the EventLogProtocol (the single
    source of truth for conversation history — see
    serving/stream/history.py::project_thread()) even though nothing is
    watching this specific SSE connection anymore.

    Two properties must hold for this to work:
      1. _check_disconnect must not Runtime.cancel() the run on a mere
         disconnect (a refresh isn't a cancel).
      2. events()'s cleanup must not cancel agent_task — cancelling it
         could abort it mid-register()/submit(), before the run is even
         enqueued. It must instead detach it so register()+submit()+tailing
         complete naturally regardless of this connection's fate.

    This test deliberately keeps the run suspended across the disconnect
    (nothing is sleeping on a wall-clock timer) and only fires the resume
    signal after confirming the SSE generator has already returned and the
    run is durably suspended (not cancelled)."""
    signal_name = "resume-me"

    @dataclass
    class SuspendingReplyAgent:
        name: str = "suspend_reply"

        @property
        def id(self) -> AgentId:
            return AgentId(type="agent", key=self.name)

        async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
            for msg in inbox:
                # Suspends here (raises SuspendInterrupt) until the signal
                # fires — exactly like ask_human's sleep_until_signal.
                await ctx.sleep_until_signal(signal_name)
                await ctx._log("text.delta", {"text": "post-resume reply"})
                await ctx.reply(msg, {"text": "post-resume reply"})

    is_disconnected = False

    async def check_disconnected() -> bool:
        return is_disconnected

    async with Runtime() as rt:
        agent = SuspendingReplyAgent()
        msg = _make_msg(agent.id)
        session = AgentStreamSession(
            runtime=rt,
            agent=agent,
            msg=msg,
            bridge=_StubBridge(),
            is_disconnected=check_disconnected,
            poll_interval=0.01,
        )

        async for ev in session.events():
            if isinstance(ev, HelloEvent):
                # Disconnect while the run is still suspended (before answer).
                is_disconnected = True

        # events() has returned. The run is suspended, NOT cancelled, and the
        # detached agent_task is still alive tailing it.
        for _ in range(100):
            if session._run_id is not None:
                break
            await asyncio.sleep(0.02)
        run_id = session._run_id
        assert run_id is not None
        for _ in range(100):
            status = await rt.scheduler.get_status(run_id)
            if status is not None and status.value == "suspended":
                break
            await asyncio.sleep(0.02)
        assert status is not None and status.value == "suspended", (
            "run should be suspended (not cancelled) after a mere disconnect"
        )

        # Now the human answers. The detached task must still be tailing (or
        # the run resumes on its own via a fresh lease either way) for the
        # post-resume reply to land durably in the EventLogProtocol.
        await rt.signal_bus.signal(run_id, signal_name, {})

        found_reply = False
        for _ in range(200):
            entries = [e async for e in rt.event_log.read(run_id)]
            if any(
                e.kind == "text.delta" and e.payload.get("text") == "post-resume reply"
                for e in entries
            ):
                found_reply = True
                break
            await asyncio.sleep(0.02)
        assert found_reply, "post-resume reply must land in the EventLogProtocol"


async def test_durable_cancel_ends_session() -> None:
    """SupervisorProtocol.cancel() — the durable, cross-replica path routes/cancel.py
    actually calls — terminates the session exactly like a same-process
    cancel does: purely via the EventLogProtocol's run.cancelled entry appearing,
    with no session-owned cancel Event/registry involved at all. This is
    what makes cancel work correctly even when POST /cancel lands on a
    different replica than the one running the SSE stream."""
    from substrate.kernel.core.identity import AgentId as _AgentId
    from substrate.kernel.runtime.supervisor import RunHandle

    @dataclass
    class HangingAgent:
        name: str = "hanging"

        @property
        def id(self) -> AgentId:
            return AgentId(type="agent", key=self.name)

        async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
            for msg in inbox:
                await asyncio.sleep(100)

    async with Runtime() as rt:
        agent = HangingAgent()
        msg = _make_msg(agent.id)
        thread_id = "test-thread-durable-cancel"
        session = AgentStreamSession(
            runtime=rt,
            agent=agent,
            msg=msg,
            bridge=_StubBridge(),
            thread_id=thread_id,
            poll_interval=0.01,
        )

        async def _cancel_once_active() -> None:
            found = None
            for _ in range(200):
                found = await rt.scheduler.find_run_for_thread(thread_id)
                if found is not None:
                    break
                await asyncio.sleep(0.01)
            assert found is not None, "run never became active for thread"
            run_id, _status = found
            # agent_id/parent_run are placeholders — SupervisorProtocol.cancel() only
            # reads handle.run_id (see routes/cancel.py for the same pattern).
            handle = RunHandle(
                run_id=run_id, agent_id=_AgentId(type="", key=""), parent_run=""
            )
            await rt.supervisor.cancel(handle, reason="test")

        asyncio.create_task(_cancel_once_active())

        events = []
        async for ev in session.events():
            events.append(ev)

        assert any(isinstance(e, RunCancelledEvent) for e in events)


async def test_disconnected_stops_local_relay_without_cancelling_run() -> None:
    """A browser disconnect (which a page refresh also triggers) must NOT
    cancel the underlying run or its persistence — only the LOCAL relay to
    this now-dead connection stops. This is the whole point of durable
    suspend/resume: a refresh must not destroy in-flight progress. Before
    this fix, disconnect unconditionally cancelled the run's own Worker
    Task via Runtime.cancel() — a race-prone "coin flip" depending on
    whether the ASGI-level teardown or this check ran first."""
    done = asyncio.Event()

    @dataclass
    class SlowAgent:
        name: str = "slow"

        @property
        def id(self) -> AgentId:
            return AgentId(type="agent", key=self.name)

        async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
            for msg in inbox:
                await asyncio.sleep(0.2)
            done.set()

    is_disconnected = False

    async def check_disconnected() -> bool:
        return is_disconnected

    async with Runtime() as rt:
        agent = SlowAgent()
        msg = _make_msg(agent.id)
        session = AgentStreamSession(
            runtime=rt,
            agent=agent,
            msg=msg,
            bridge=_StubBridge(),
            is_disconnected=check_disconnected,
            poll_interval=0.01,
        )

        events = []
        async for ev in session.events():
            events.append(ev)
            if isinstance(ev, HelloEvent):
                is_disconnected = True

        # The local stream still terminates promptly for this connection...
        assert any(isinstance(e, RunCancelledEvent) for e in events)
        # ...but the run itself was never told to stop, and finishes on its
        # own — proving Runtime.cancel() was NOT called on disconnect.
        await asyncio.wait_for(done.wait(), timeout=3.0)
        run_id = session._run_id
        assert run_id is not None
        for _ in range(100):
            status = await rt.scheduler.get_status(run_id)
            if status is not None and status.value == "completed":
                break
            await asyncio.sleep(0.02)
        assert status is not None and status.value == "completed"


# ---------------------------------------------------------------------------
# tail_wire_events — reconnect tailer (GET /stream/{thread_id})
# ---------------------------------------------------------------------------


async def test_tail_wire_events_skips_non_streamable_kinds_without_crashing() -> None:
    """The exact bug this guards against: wire_from_log() returns None for
    any kind outside STREAMING_KINDS (run.started, run.suspended,
    run.resumed, llm.call, effect.result, ...) — a naive tailer that calls
    `event.model_dump()` without a None-check crashes on the very first such
    entry, which is nearly guaranteed to appear before any real content
    (e.g. run.resumed fires immediately on reconnect-after-answer). This
    interleaves several non-streamable kinds among real ones and asserts
    they're silently skipped, not fatal."""

    async def agent_run(ctx: RunContext, inbox: list[Message]) -> None:
        await ctx._log("run.started", {})
        await ctx._log("effect.result", {"value": {}})
        await ctx._log("text.delta", {"text": "hello "})
        await ctx._log("llm.call", {"model": "x", "tokens": 1})
        await ctx._log("text.delta", {"text": "world"})
        await ctx._log("run.completed", {})

    class InlineAgent:
        id = AgentId(type="agent", key="tail_wire_test")
        run = staticmethod(agent_run)

    async with Runtime() as rt:
        agent = InlineAgent()
        await rt.register(agent)
        msg = Message(
            target=agent.id,
            payload=ChatPayload(
                message=ChatMessage(role=Role.USER, content=[TextBlock(text="hi")])
            ),
        )
        run_id = await rt.submit(agent.id, msg)

        for _ in range(200):
            status = await rt.scheduler.get_status(run_id)
            if status is not None and status.value == "completed":
                break
            await asyncio.sleep(0.01)
        assert status is not None and status.value == "completed"

        events = [ev async for ev in tail_wire_events(rt.event_log, run_id, from_seq=0)]

    assert any(isinstance(e, RunCompletedEvent) for e in events)
    text_events = [e for e in events if getattr(e, "type", None) == "text.delta"]
    assert len(text_events) == 2


async def test_tail_wire_events_maps_run_failed() -> None:
    async def agent_run(ctx: RunContext, inbox: list[Message]) -> None:
        raise RuntimeError("boom")

    class CrashInlineAgent:
        id = AgentId(type="agent", key="tail_wire_fail_test")
        run = staticmethod(agent_run)

    async with Runtime() as rt:
        agent = CrashInlineAgent()
        await rt.register(agent)
        msg = Message(
            target=agent.id,
            payload=ChatPayload(
                message=ChatMessage(role=Role.USER, content=[TextBlock(text="hi")])
            ),
        )
        # max_retries=0: this test is about tail_wire_events mapping
        # run.failed, not retry semantics — a default retry policy would
        # back the run off and retry rather than terminal-failing on the
        # first attempt.
        run_id = await rt.submit(agent.id, msg, max_retries=0)

        for _ in range(200):
            status = await rt.scheduler.get_status(run_id)
            if status is not None and status.value == "failed":
                break
            await asyncio.sleep(0.01)
        assert status is not None and status.value == "failed"

        events = [ev async for ev in tail_wire_events(rt.event_log, run_id, from_seq=0)]

    assert len(events) == 1
    assert isinstance(events[0], RunFailedEvent)
