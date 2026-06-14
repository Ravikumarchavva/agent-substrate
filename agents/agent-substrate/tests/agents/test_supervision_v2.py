"""Supervision v2 — runtime enforcement tests.

Covers:
1. Unexpected agent crash is recorded as ``agent_crashed`` status (not silently swallowed).
2. ``HistoryRetention.RUN`` on a ``ContextConfig`` triggers ``clear_run`` after the run
   completes, leaving no run-scoped history behind.
3. ``HistoryRetention.PERMANENT`` does NOT trigger ``clear_run`` — history survives.
"""

from __future__ import annotations

import asyncio


from ravi.agents.runtime import Runtime
from ravi.kernel.agent.supervision import HistoryRetention
from ravi.kernel.core.identity import AgentId
from ravi.kernel.messaging.message import DataPayload, Message


def _agent_id(name: str) -> AgentId:
    return AgentId(type="agent", key=name)


def _msg(target: AgentId) -> Message:
    return Message(target=target, payload=DataPayload(data={}))


# ---------------------------------------------------------------------------
# 1. Crash → ``agent_crashed`` status in EventLog
# ---------------------------------------------------------------------------


async def test_crash_records_agent_crashed_status() -> None:
    """An unexpected exception inside agent.run() is surfaced as agent_crashed."""

    class BombAgent:
        id = _agent_id("bomb")

        async def run(self, ctx: object, inbox: list[Message]) -> None:
            raise RuntimeError("boom!")

    bomb = BombAgent()
    async with Runtime() as rt:
        await rt.register(bomb)
        run_id = await rt.submit(bomb.id, _msg(bomb.id))

        async for entry in rt.event_log.tail(run_id):
            if entry.kind in ("run.completed", "run.failed", "run.cancelled"):
                assert entry.kind == "run.failed"
                assert entry.payload.get("status") == "agent_crashed"
                assert "boom!" in entry.payload.get("error", "")
                break


async def test_guardrail_trip_records_guardrail_tripped_status() -> None:
    """MiddlewareTermination is recorded as guardrail_tripped, not agent_crashed."""
    from ravi.kernel.core.errors import MiddlewareTermination

    class GuardrailAgent:
        id = _agent_id("guardrail")

        async def run(self, ctx: object, inbox: list[Message]) -> None:
            raise MiddlewareTermination("blocked!")

    agent = GuardrailAgent()
    async with Runtime() as rt:
        await rt.register(agent)
        run_id = await rt.submit(agent.id, _msg(agent.id))

        async for entry in rt.event_log.tail(run_id):
            if entry.kind in ("run.completed", "run.failed", "run.cancelled"):
                assert entry.kind == "run.failed"
                assert entry.payload.get("status") == "guardrail_tripped"
                break


async def test_budget_exhausted_records_budget_exhausted_status() -> None:
    """BudgetExhaustedError is recorded as budget_exhausted."""
    from ravi.kernel.core.errors import BudgetExhaustedError

    class BudgetAgent:
        id = _agent_id("budgeter")

        async def run(self, ctx: object, inbox: list[Message]) -> None:
            raise BudgetExhaustedError("too many tokens")

    agent = BudgetAgent()
    async with Runtime() as rt:
        await rt.register(agent)
        run_id = await rt.submit(agent.id, _msg(agent.id))

        async for entry in rt.event_log.tail(run_id):
            if entry.kind in ("run.completed", "run.failed", "run.cancelled"):
                assert entry.kind == "run.failed"
                assert entry.payload.get("status") == "budget_exhausted"
                break


# ---------------------------------------------------------------------------
# 2. HistoryRetention.RUN → clear_run after completion
# ---------------------------------------------------------------------------


async def test_history_retention_run_clears_after_completion() -> None:
    """Agents with HistoryRetention.RUN have run-scoped history cleared on completion."""
    from ravi.agents.context.context import ContextConfig
    from ravi.agents.context.history import InMemoryHistoryProvider
    from ravi.agents.core._loop import persist_turns
    from ravi.kernel.core.content import ChatMessage, Role, TextBlock

    history = InMemoryHistoryProvider()
    ctx_cfg = ContextConfig(history, retention=HistoryRetention.RUN)

    written_session: list[str] = []
    written_run: list[str] = []

    class TransientAgent:
        id = _agent_id("transient")
        _context = ctx_cfg

        async def run(self, ctx: object, inbox: list[Message]) -> None:
            run_id: str = ctx.run_id  # type: ignore[attr-defined]
            session_id = inbox[0].correlation_id or run_id
            written_session.append(session_id)
            written_run.append(run_id)
            turn = ChatMessage(role=Role.USER, content=[TextBlock(text="hello")])
            await persist_turns(ctx_cfg, self.id, session_id, run_id, [turn])

    agent = TransientAgent()
    async with Runtime() as rt:
        await rt.register(agent)
        done = asyncio.Event()

        msg = _msg(agent.id)
        run_id = await rt.submit(agent.id, msg)

        async for entry in rt.event_log.tail(run_id):
            if entry.kind in ("run.completed", "run.failed", "run.cancelled"):
                assert entry.kind == "run.completed"
                done.set()
                break

        await asyncio.wait_for(done.wait(), timeout=3.0)

    # After completion the Worker should have called clear_run.
    # Verify: fetching history for the session returns nothing.
    session_id = written_session[0] if written_session else run_id
    remaining = await history.get_messages(agent.id, session_id=session_id)
    assert remaining == [], f"Expected empty history after RUN retention cleanup, got {remaining}"


# ---------------------------------------------------------------------------
# 3. HistoryRetention.PERMANENT — history survives run completion
# ---------------------------------------------------------------------------


async def test_history_retention_permanent_survives_completion() -> None:
    """Agents with HistoryRetention.PERMANENT (default) keep history after run ends."""
    from ravi.agents.context.context import ContextConfig
    from ravi.agents.context.history import InMemoryHistoryProvider
    from ravi.agents.core._loop import persist_turns
    from ravi.kernel.core.content import ChatMessage, Role, TextBlock

    history = InMemoryHistoryProvider()
    ctx_cfg = ContextConfig(history, retention=HistoryRetention.PERMANENT)

    captured_session: list[str] = []
    captured_run: list[str] = []

    class PermanentAgent:
        id = _agent_id("permanent")
        _context = ctx_cfg

        async def run(self, ctx: object, inbox: list[Message]) -> None:
            run_id: str = ctx.run_id  # type: ignore[attr-defined]
            session_id = inbox[0].correlation_id or run_id
            captured_session.append(session_id)
            captured_run.append(run_id)
            turn = ChatMessage(role=Role.USER, content=[TextBlock(text="remember me")])
            await persist_turns(ctx_cfg, self.id, session_id, run_id, [turn])

    agent = PermanentAgent()
    async with Runtime() as rt:
        await rt.register(agent)
        run_id = await rt.submit(agent.id, _msg(agent.id))

        async for entry in rt.event_log.tail(run_id):
            if entry.kind in ("run.completed", "run.failed", "run.cancelled"):
                assert entry.kind == "run.completed"
                break

    session_id = captured_session[0] if captured_session else run_id
    remaining = await history.get_messages(agent.id, session_id=session_id)
    assert len(remaining) == 1
    assert remaining[0].role == Role.USER
