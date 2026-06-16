"""Kernel-native AgentStreamSession tests.

Drives the session with stub kernel agents and a real in-process Runtime.
No mocks — the session, Runtime, and EventLog all run as they would in prod.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from ravi.agents.runtime.context import RunContext
from ravi.agents.runtime.runtime import Runtime
from ravi.kernel.core.content import ChatMessage, Role, TextBlock
from ravi.kernel.core.identity import AgentId
from ravi.kernel.messaging.message import ChatPayload, Message
from ravi.serving.monolith.sse.bridge import BRIDGE_DONE
from ravi.serving.protocol import (
    HelloEvent,
    RunCompletedEvent,
    RunFailedEvent,
    ToolResultEvent,
    TurnCompletedEvent,
    WireEvent,
    RunCancelledEvent,
)
from ravi.serving.stream.session import AgentStreamSession


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

    Logs a ``text.delta`` entry so the session persister accumulates text —
    mirroring what ``ctx.llm()`` does in a real ``ReActAgent`` run.
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


async def test_persister_called_on_turn_complete() -> None:
    persist_calls: list[TurnCompletedEvent] = []

    class _FakePersister:
        async def persist_turn(self, event: TurnCompletedEvent) -> None:
            persist_calls.append(event)

        async def persist_tool(self, event: ToolResultEvent) -> None:
            pass

    async with Runtime() as rt:
        agent = ReplyAgent(reply="persisted text", name="persist_agent")
        msg = _make_msg(agent.id)
        session = AgentStreamSession(
            runtime=rt,
            agent=agent,
            msg=msg,
            bridge=_StubBridge(),
            persister=_FakePersister(),
        )
        await _collect(session)

    assert len(persist_calls) == 1
    assert "persisted text" in persist_calls[0].text


async def test_cancel_event_cancels_run() -> None:
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
        cancel_event = asyncio.Event()
        session = AgentStreamSession(
            runtime=rt,
            agent=agent,
            msg=msg,
            bridge=_StubBridge(),
            cancel_event=cancel_event,
            poll_interval=0.01,
        )

        # Start consuming events, and trigger cancel on hello
        events = []
        async for ev in session.events():
            events.append(ev)
            if isinstance(ev, HelloEvent):
                cancel_event.set()

        assert any(isinstance(e, RunCancelledEvent) for e in events)


async def test_disconnected_callback_cancels_run() -> None:
    @dataclass
    class HangingAgent:
        name: str = "hanging"

        @property
        def id(self) -> AgentId:
            return AgentId(type="agent", key=self.name)

        async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
            for msg in inbox:
                await asyncio.sleep(100)

    is_disconnected = False

    async def check_disconnected() -> bool:
        return is_disconnected

    async with Runtime() as rt:
        agent = HangingAgent()
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

        assert any(isinstance(e, RunCancelledEvent) for e in events)
