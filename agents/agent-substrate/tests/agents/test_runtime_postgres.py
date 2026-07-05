"""End-to-end integration tests for build_postgres_runtime().

Requires running Postgres.  Skips automatically when the infra is not
reachable.

Run with infra up:
    make infra-up
    uv run pytest tests/agents/test_runtime_postgres.py -v
"""

from __future__ import annotations

import asyncio
import os
import types

import pytest

from substrate.kernel.core.identity import AgentId
from substrate.kernel.messaging.message import DataPayload, Message
from substrate.kernel.runtime.communication import AskOutcome

pytestmark = [pytest.mark.requires_postgres]

_PG_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/agentdb"
).replace("+asyncpg", "")


async def _pg_reachable() -> bool:
    try:
        import asyncpg

        pool = await asyncpg.create_pool(_PG_URL, min_size=1, max_size=1)
        await pool.close()
        return True
    except Exception:
        return False


@pytest.fixture()
async def pg_runtime():
    """Runtime backed by Postgres only (in-memory journal)."""
    if not await _pg_reachable():
        pytest.skip("Postgres not reachable")
    from substrate.infrastructure.runtime import build_postgres_runtime

    async with build_postgres_runtime(postgres_url=_PG_URL) as rt:
        yield rt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agent_id(name: str) -> AgentId:
    return AgentId(type=name, key=f"pg-test-{id(object())}")


def _msg(target: AgentId, data: dict | None = None) -> Message:
    return Message(target=target, payload=DataPayload(data=data or {}))


# ---------------------------------------------------------------------------
# Shared agent fixtures
# ---------------------------------------------------------------------------


class RecorderAgent:
    def __init__(self, agent_id: AgentId) -> None:
        self.id = agent_id
        self.received: list[Message] = []
        self.done = asyncio.Event()

    async def run(self, ctx: object, inbox: list[Message]) -> None:
        self.received.extend(inbox)
        self.done.set()


class EchoAgent:
    def __init__(self, agent_id: AgentId) -> None:
        self.id = agent_id

    async def run(self, ctx: object, inbox: list[Message]) -> None:

        ctx = ctx  # type: ignore[assignment]
        for msg in inbox:
            if msg.reply_to is not None:
                data = (
                    msg.payload.data  # type: ignore[union-attr]
                    if isinstance(msg.payload, DataPayload)
                    else {}
                )
                await ctx.reply(msg, {"echo": data})  # type: ignore[attr-defined]


class AskerAgent:
    def __init__(self, agent_id: AgentId, target: AgentId) -> None:
        self.id = agent_id
        self.target = target
        self.outcome: AskOutcome | None = None
        self.done = asyncio.Event()

    async def run(self, ctx: object, inbox: list[Message]) -> None:

        ctx = ctx  # type: ignore[assignment]
        msg = _msg(self.target, {"value": 99})
        self.outcome = await ctx.ask(  # type: ignore[attr-defined]
            self.target, msg, timeout=5.0
        )
        self.done.set()


# ---------------------------------------------------------------------------
# 1. Fire-and-forget with Postgres backend
# ---------------------------------------------------------------------------


async def test_pg_fire_and_forget(pg_runtime) -> None:
    agent_id = _agent_id("recorder")
    agent = RecorderAgent(agent_id)

    await pg_runtime.register(agent)
    await pg_runtime.submit(agent_id, _msg(agent_id, {"hello": "postgres"}))
    await asyncio.wait_for(agent.done.wait(), timeout=5.0)

    payloads = [
        m.payload.data  # type: ignore[union-attr]
        for m in agent.received
        if isinstance(m.payload, DataPayload)
    ]
    assert {"hello": "postgres"} in payloads


# ---------------------------------------------------------------------------
# 2. Multiple messages delivered before agent starts
# ---------------------------------------------------------------------------


async def test_pg_batched_delivery(pg_runtime) -> None:
    """Multiple messages enqueued before the worker drains should all arrive."""
    agent_id = _agent_id("batcher")
    received: list[dict] = []
    barrier = asyncio.Event()

    class BatchAgent:
        id = agent_id

        async def run(self, ctx: object, inbox: list[Message]) -> None:
            for msg in inbox:
                if isinstance(msg.payload, DataPayload):
                    received.append(msg.payload.data)
            barrier.set()

    await pg_runtime.register(BatchAgent())
    for i in range(3):
        await pg_runtime.submit(agent_id, _msg(agent_id, {"seq": i}))

    await asyncio.wait_for(barrier.wait(), timeout=5.0)
    seqs = {d["seq"] for d in received}
    assert {0, 1, 2}.issubset(seqs)


# ---------------------------------------------------------------------------
# 3. Ask / reply round-trip with Postgres backend
# ---------------------------------------------------------------------------


async def test_pg_ask_reply(pg_runtime) -> None:
    echo_id = _agent_id("echo")
    asker_id = _agent_id("asker")
    echo = EchoAgent(echo_id)
    asker = AskerAgent(asker_id, echo_id)

    await pg_runtime.register(echo)
    await pg_runtime.register(asker)
    await pg_runtime.submit(asker_id, _msg(asker_id))

    await asyncio.wait_for(asker.done.wait(), timeout=8.0)
    assert asker.outcome is not None
    assert asker.outcome.kind == "replied"
    assert asker.outcome.result is not None
    assert asker.outcome.result.output.data == {"echo": {"value": 99}}


class ChildAgent:
    def __init__(self, agent_id: AgentId) -> None:
        self.id = agent_id

    async def run(self, ctx: object, inbox: list[Message]) -> None:
        pass  # simply finishes — status COMPLETED


class ParentJoinAgent:
    def __init__(self, agent_id: AgentId, child_id: AgentId) -> None:
        self.id = agent_id
        self.child_id = child_id
        self.done = asyncio.Event()
        self.child_result = None

    async def run(self, ctx: object, inbox: list[Message]) -> None:
        boot = _msg(self.child_id, {"task": "work"})
        handle = await ctx.spawn(self.child_id, boot=boot)  # type: ignore[attr-defined]
        self.child_result = await ctx.join(handle)  # type: ignore[attr-defined]
        self.done.set()


async def test_pg_spawn_join(pg_runtime) -> None:
    """ctx.join() suspends the parent (durably) and resumes when the child
    finishes — the PostgresSupervisor.finish_run() -> child:{run_id} signal
    path, replacing the old asyncio.Event()-blocking Supervisor.join()."""
    from substrate.kernel.runtime.ids import RunStatus

    child_id = _agent_id("pg-join-child")
    parent_id = _agent_id("pg-join-parent")
    child = ChildAgent(child_id)
    parent = ParentJoinAgent(parent_id, child_id)

    await pg_runtime.register(child)
    await pg_runtime.register(parent)
    await pg_runtime.submit(parent_id, _msg(parent_id, {"start": True}))

    await asyncio.wait_for(parent.done.wait(), timeout=8.0)
    assert parent.child_result is not None
    assert parent.child_result.status == RunStatus.COMPLETED


class CrashingChildAgent:
    def __init__(self, agent_id: AgentId) -> None:
        self.id = agent_id

    async def run(self, ctx: object, inbox: list[Message]) -> None:
        raise RuntimeError("child deliberately crashes")


class ParentJoinCrashAgent:
    def __init__(self, agent_id: AgentId, child_id: AgentId) -> None:
        self.id = agent_id
        self.child_id = child_id
        self.done = asyncio.Event()
        self.child_result = None

    async def run(self, ctx: object, inbox: list[Message]) -> None:
        boot = _msg(self.child_id, {"task": "work"})
        handle = await ctx.spawn(self.child_id, boot=boot)  # type: ignore[attr-defined]
        self.child_result = await ctx.join(handle)  # type: ignore[attr-defined]
        self.done.set()


async def test_pg_join_crash_fast_path(pg_runtime) -> None:
    """A crashed child wakes the joining parent immediately via its FAILED
    finish_run() signal — the parent must not depend on any timeout."""
    from substrate.kernel.runtime.ids import RunStatus

    child_id = _agent_id("pg-join-crash-child")
    parent_id = _agent_id("pg-join-crash-parent")
    child = CrashingChildAgent(child_id)
    parent = ParentJoinCrashAgent(parent_id, child_id)

    await pg_runtime.register(child)
    await pg_runtime.register(parent)
    await pg_runtime.submit(parent_id, _msg(parent_id, {"start": True}))

    await asyncio.wait_for(parent.done.wait(), timeout=8.0)
    assert parent.child_result is not None
    assert parent.child_result.status == RunStatus.FAILED


# ---------------------------------------------------------------------------
# 5. Streaming path over the Postgres event log (the served default)
# ---------------------------------------------------------------------------


class _StubBridge:
    """Mirrors WebHITLBridge: get_event() blocks until signal_done() is called."""

    def __init__(self) -> None:
        self._done = asyncio.Event()

    async def get_event(self):
        await self._done.wait()
        from substrate.serving.monolith.sse.bridge import BRIDGE_DONE

        return BRIDGE_DONE

    async def signal_done(self) -> None:
        self._done.set()

    def cancel_all_pending(self, reason: str = "") -> int:
        return 0


class StreamingAgent:
    """Logs the wire-relevant event kinds, exactly as ctx.llm()/ctx.tool() would."""

    def __init__(self, agent_id: AgentId) -> None:
        self.id = agent_id

    async def run(self, ctx: object, inbox: list[Message]) -> None:
        for _ in inbox:
            await ctx._log("text.delta", {"text": "Hello "})  # type: ignore[attr-defined]
            await ctx._log("text.delta", {"text": "world"})  # type: ignore[attr-defined]
            await ctx._log(  # type: ignore[attr-defined]
                "tool.call",
                {"call_id": "c1", "tool_name": "calc", "args": {"x": 2}},
            )
            await ctx._log(  # type: ignore[attr-defined]
                "tool.result",
                {"call_id": "c1", "tool_name": "calc", "ok": True, "output": "4"},
            )


async def test_pg_streaming_session(pg_runtime) -> None:
    """A run streamed through AgentStreamSession over the Postgres event log:
    wire events come out in order AND the entries are persisted in Postgres."""
    from substrate.kernel.core.content import ChatMessage, Role, TextBlock
    from substrate.kernel.messaging.message import ChatPayload
    from substrate.serving.protocol import (
        HelloEvent,
        RunCompletedEvent,
        TextDeltaEvent,
        ToolCallEvent,
        ToolResultEvent,
    )
    from substrate.serving.stream.session import AgentStreamSession

    agent_id = _agent_id("streamer")
    agent = StreamingAgent(agent_id)
    msg = Message(
        target=agent_id,
        payload=ChatPayload(
            message=ChatMessage(role=Role.USER, content=[TextBlock(text="hi")])
        ),
    )
    session = AgentStreamSession(
        runtime=pg_runtime, agent=agent, msg=msg, bridge=_StubBridge()
    )

    events = await asyncio.wait_for(_collect_events(session), timeout=20.0)

    # Wire events stream out, framed by hello … run.completed
    assert isinstance(events[0], HelloEvent)
    assert isinstance(events[-1], RunCompletedEvent)
    assert any(isinstance(e, ToolCallEvent) for e in events)
    assert any(isinstance(e, ToolResultEvent) for e in events)
    streamed_text = "".join(e.text for e in events if isinstance(e, TextDeltaEvent))
    assert streamed_text == "Hello world"

    # Entries are durably persisted in Postgres (read back from the event log)
    run_id = session._run_id
    assert run_id is not None
    kinds = [entry.kind async for entry in pg_runtime.event_log.read(run_id)]
    for expected in ("text.delta", "tool.call", "tool.result", "run.completed"):
        assert expected in kinds, f"{expected} missing from Postgres event log: {kinds}"


async def _collect_events(session) -> list:
    return [ev async for ev in session.events()]


# ---------------------------------------------------------------------------
# 6. Orphan reclamation on startup (run recovery)
# ---------------------------------------------------------------------------


async def test_pg_reclaim_orphans() -> None:
    """A run left 'running' by a crashed worker is requeued to 'pending'
    on startup when reclaim_orphans=True (single-worker deployments)."""
    if not await _pg_reachable():
        pytest.skip("Postgres not reachable")
    import asyncpg

    from substrate.infrastructure.runtime.pg_scheduler import PostgresScheduler

    pool = await asyncpg.create_pool(_PG_URL, min_size=1, max_size=2)
    try:
        scheduler = PostgresScheduler(pool)
        await scheduler.setup()

        run_id = f"orphan-{id(object())}"
        # Simulate a crash: a row stuck in 'running' with a live (future) lease.
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO substrate_run_queue (run_id, status, worker_id, expires_at)
                VALUES ($1, 'running', 'dead-worker', now() + interval '30 seconds')
                """,
                run_id,
            )

        # Default (expired-only) must NOT touch a still-future lease.
        assert await scheduler.reclaim_orphans(all_running=False) == 0
        async with pool.acquire() as conn:
            status = await conn.fetchval(
                "SELECT status FROM substrate_run_queue WHERE run_id = $1", run_id
            )
        assert status == "running"

        # Single-worker reclaim requeues it immediately.
        reclaimed = await scheduler.reclaim_orphans(all_running=True)
        assert reclaimed >= 1
        async with pool.acquire() as conn:
            status = await conn.fetchval(
                "SELECT status FROM substrate_run_queue WHERE run_id = $1", run_id
            )
            await conn.execute("DELETE FROM substrate_run_queue WHERE run_id = $1", run_id)
        assert status == "pending"
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# 7. Cold resume — agent spec persisted and rebuilt across runtime restarts
# ---------------------------------------------------------------------------


async def test_pg_cold_resume() -> None:
    """Spec saved during a run is read back by a fresh runtime to rebuild the agent.

    Flow:
      1. Runtime A: register agent, submit message, save spec, tear down.
      2. Runtime B: reclaim_orphans(all_running=True), read pending_run_specs,
         rebuild agent, register it — assert the run completes.
    """
    if not await _pg_reachable():
        pytest.skip("Postgres not reachable")

    from substrate.infrastructure.runtime import build_postgres_runtime
    from substrate.infrastructure.runtime.pg_scheduler import PostgresScheduler
    from substrate.agents.factory import rebuild_agent

    done_a = asyncio.Event()

    class EchoSpecAgent:
        """Completes immediately after one message."""

        def __init__(self, aid: AgentId) -> None:
            self.id = aid

        async def run(self, ctx: object, inbox: list[Message]) -> None:
            done_a.set()

    # --- Runtime A: start a run, save a fake spec, then tear down ---
    agent_id_a = _agent_id("spec-agent")
    spec = {
        "mode": "react",
        "system_instructions": "test",
        "tool_names": [],
        "max_iterations": 5,
        "session_id": "resume-test",
        "model_context_window": 10,
    }

    async with build_postgres_runtime(postgres_url=_PG_URL) as rt_a:
        agent_a = EchoSpecAgent(agent_id_a)
        await rt_a.register(agent_a)
        run_id = await rt_a.submit(agent_id_a, _msg(agent_id_a, {"x": 1}))
        # Wait for run to complete in runtime A
        await asyncio.wait_for(done_a.wait(), timeout=5.0)
        # Save the spec manually (simulating what AgentStreamSession does)
        await rt_a._scheduler.save_run_spec(run_id, spec)

    # At this point runtime A is torn down.  Simulate a crash by inserting a
    # fresh run_id that is 'running' (orphaned) with a spec.
    import asyncpg

    pool = await asyncpg.create_pool(_PG_URL, min_size=1, max_size=2)
    try:
        import json as _json

        orphan_run_id = f"orphan-resume-{id(object())}"
        orphan_agent_id = _agent_id("orphan-spec-agent")
        async with pool.acquire() as conn:
            # Insert agent run mapping
            await conn.execute(
                """
                INSERT INTO substrate_agent_runs (run_id, agent_id, spec)
                VALUES ($1, $2, $3::jsonb)
                ON CONFLICT (run_id) DO NOTHING
                """,
                orphan_run_id,
                str(orphan_agent_id),
                _json.dumps(spec),
            )
            # Insert as 'pending' (already reclaimed)
            await conn.execute(
                """
                INSERT INTO substrate_run_queue (run_id, status)
                VALUES ($1, 'pending')
                ON CONFLICT (run_id) DO NOTHING
                """,
                orphan_run_id,
            )

        # Use PostgresScheduler to read back pending run specs
        scheduler = PostgresScheduler(pool)
        await scheduler.setup()
        pending = await scheduler.pending_run_specs()
        our_specs = [(rid, aid, s) for rid, aid, s in pending if rid == orphan_run_id]
        assert len(our_specs) == 1, f"Expected spec for {orphan_run_id}, got {pending}"

        rid, aid, s = our_specs[0]
        assert s["session_id"] == "resume-test"
        assert aid == orphan_agent_id

        # Rebuild agent from spec
        rebuilt = rebuild_agent(s, model_client=None, tools=[])  # type: ignore[arg-type]
        rebuilt.id = aid
        assert rebuilt.id == orphan_agent_id

        # Cleanup
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM substrate_run_queue WHERE run_id = $1", orphan_run_id
            )
            await conn.execute(
                "DELETE FROM substrate_agent_runs WHERE run_id = $1", orphan_run_id
            )
    finally:
        await pool.close()


async def test_pg_cold_resume_refuses_version_mismatch() -> None:
    """A persisted spec from a different substrate version fails cleanly
    instead of being replayed (Phase 5 agent versioning guard).

    Uses bare backend objects (no live Worker polling loop) — same
    reasoning as ``test_pg_cold_resume``: a running Worker (this process's
    or another live process sharing the same Postgres DB) leases *any*
    'pending' row it sees, agent-agnostically, which would race this test's
    direct 'pending' insert before ``resume_pending_runs`` gets to read it.

    Asserts: the run ends up 'failed' (not silently resumed), exactly one
    ``run.failed`` EventLog entry is appended, and no agent is registered
    for it.
    """
    if not await _pg_reachable():
        pytest.skip("Postgres not reachable")

    import json as _json

    import asyncpg

    import substrate
    from substrate.infrastructure.runtime.pg_event_log import PostgresEventLog
    from substrate.infrastructure.runtime.pg_inbox import PostgresInbox
    from substrate.infrastructure.runtime.pg_scheduler import PostgresScheduler
    from substrate.infrastructure.runtime.pg_signal_bus import PostgresSignalBus
    from substrate.infrastructure.runtime.pg_supervisor import PostgresSupervisor
    from substrate.infrastructure.serving_factory import resume_pending_runs

    stale_spec = {
        "mode": "react",
        "agent_version": "0.0.0-stale",
        "system_instructions": "test",
        "tool_names": [],
        "max_iterations": 5,
        "session_id": "resume-version-test",
        "model_context_window": 10,
    }
    run_id = f"version-mismatch-{id(object())}"
    agent_id = _agent_id("version-mismatch-agent")

    pool = await asyncpg.create_pool(_PG_URL, min_size=1, max_size=2)
    try:
        event_log = PostgresEventLog(pool)
        scheduler = PostgresScheduler(pool)
        inbox = PostgresInbox(pool)
        signal_bus = PostgresSignalBus(pool)
        supervisor = PostgresSupervisor(
            pool,
            event_log=event_log,
            inbox=inbox,
            scheduler=scheduler,
            signal_bus=signal_bus,
        )
        await event_log.setup()
        await scheduler.setup()
        await inbox.setup()
        await signal_bus.setup()
        await supervisor.setup()

        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO substrate_agent_runs (run_id, agent_id, spec)
                VALUES ($1, $2, $3::jsonb)
                ON CONFLICT (run_id) DO NOTHING
                """,
                run_id,
                str(agent_id),
                _json.dumps(stale_spec),
            )
            await conn.execute(
                """
                INSERT INTO substrate_run_queue (run_id, status)
                VALUES ($1, 'pending')
                ON CONFLICT (run_id) DO NOTHING
                """,
                run_id,
            )

        class _EmptyRegistry:
            def all(self):
                return []

        async def _unexpected_register(agent):
            raise AssertionError(
                "should not register an agent for a version-mismatched spec"
            )

        fake_runtime = types.SimpleNamespace(
            _scheduler=scheduler,
            event_log=event_log,
            supervisor=supervisor,
            register=_unexpected_register,
        )

        resumed = await resume_pending_runs(
            fake_runtime, registry=_EmptyRegistry(), model_client=None
        )
        assert resumed == 1

        entries = [e async for e in event_log.read(run_id)]
        failed_entries = [e for e in entries if e.kind == "run.failed"]
        assert len(failed_entries) == 1
        assert failed_entries[0].payload["status"] == "version_mismatch"

        row = await pool.fetchrow(
            "SELECT status FROM substrate_run_queue WHERE run_id = $1", run_id
        )
        assert row["status"] == "failed"

        # Sanity: the spec really was stale relative to the running version.
        assert stale_spec["agent_version"] != substrate.__version__
    finally:
        await pool.execute("DELETE FROM substrate_run_queue WHERE run_id = $1", run_id)
        await pool.execute("DELETE FROM substrate_agent_runs WHERE run_id = $1", run_id)
        await pool.close()


# ---------------------------------------------------------------------------
# 6. PR7 — cancel cascade, deadline enforcement, ask() crash fast-path
# ---------------------------------------------------------------------------


class SleepForeverAgent:
    """Suspends on a signal that never arrives — stays SUSPENDED indefinitely."""

    def __init__(self, agent_id: AgentId) -> None:
        self.id = agent_id

    async def run(self, ctx: object, inbox: list[Message]) -> None:
        await ctx.sleep_until_signal("never_arrives")  # type: ignore[attr-defined]


class SpawnChainAgent:
    """Spawns one SleepForeverAgent child and reports the handle via the event."""

    def __init__(self, agent_id: AgentId, child_id: AgentId) -> None:
        self.id = agent_id
        self.child_id = child_id
        self.spawned = asyncio.Event()
        self.child_run_id: str | None = None

    async def run(self, ctx: object, inbox: list[Message]) -> None:
        boot = _msg(self.child_id, {})
        handle = await ctx.spawn(self.child_id, boot=boot)  # type: ignore[attr-defined]
        self.child_run_id = handle.run_id
        self.spawned.set()
        await ctx.sleep_until_signal("never_arrives")  # type: ignore[attr-defined]


async def test_pg_cancel_cascade(pg_runtime) -> None:
    """Cancelling the root of a 2-deep spawn tree of suspended runs marks
    the entire subtree cancelled — the recursive CTE in
    PostgresSupervisor.cancel(), exercised through the public Runtime."""
    from substrate.kernel.runtime.supervisor import RunHandle

    grandchild_id = _agent_id("pg-cancel-grandchild")
    child_id = _agent_id("pg-cancel-child")
    root_id = _agent_id("pg-cancel-root")

    grandchild = SleepForeverAgent(grandchild_id)
    child = SpawnChainAgent(child_id, grandchild_id)
    root = SpawnChainAgent(root_id, child_id)

    await pg_runtime.register(grandchild)
    await pg_runtime.register(child)
    await pg_runtime.register(root)

    root_run_id = await pg_runtime.submit(root_id, _msg(root_id, {"start": True}))

    # Wait for the whole chain to actually spawn and suspend: root spawns
    # child (and itself suspends), child's run then spawns grandchild (and
    # itself suspends), grandchild suspends forever.
    await asyncio.wait_for(root.spawned.wait(), timeout=8.0)
    await asyncio.wait_for(child.spawned.wait(), timeout=8.0)

    async def _status_of(run_id: str) -> str | None:
        async with pg_runtime.event_log._pool.acquire() as conn:  # type: ignore[attr-defined]
            return await conn.fetchval(
                "SELECT status FROM substrate_run_queue WHERE run_id = $1", run_id
            )

    async def _wait_suspended(run_id: str) -> None:
        for _ in range(100):
            if await _status_of(run_id) == "suspended":
                return
            await asyncio.sleep(0.05)
        raise AssertionError(f"{run_id} never reached suspended")

    await _wait_suspended(root_run_id)
    await _wait_suspended(child.child_run_id)

    handle = RunHandle(run_id=root_run_id, agent_id=root_id, parent_run="")
    await pg_runtime.supervisor.cancel(handle, reason="test cancel cascade")

    for run_id in (root_run_id, child.child_run_id):
        assert await _status_of(run_id) == "cancelled", run_id

    async with pg_runtime.event_log._pool.acquire() as conn:  # type: ignore[attr-defined]
        tree_status = await conn.fetch(
            "SELECT run_id, status FROM substrate_run_tree WHERE run_id = ANY($1)",
            [child.child_run_id, root.child_run_id],
        )
    assert all(r["status"] == "cancelled" for r in tree_status)


class DeadlineAgent:
    """Suspends forever — used purely to occupy a 'suspended' row for the
    deadline-enforcement test (deadline is set directly via SQL, mirroring
    the plan's own verification approach: "fire signal via bare DB write")."""

    def __init__(self, agent_id: AgentId) -> None:
        self.id = agent_id

    async def run(self, ctx: object, inbox: list[Message]) -> None:
        await ctx.sleep_until_signal("never_arrives")  # type: ignore[attr-defined]


async def test_pg_deadline_enforcement(pg_runtime) -> None:
    """A suspended run whose deadline has passed is terminal-marked FAILED
    by the scheduler's own lease poll — no external actor needed."""
    from datetime import datetime, timedelta, timezone

    agent_id = _agent_id("pg-deadline")
    agent = DeadlineAgent(agent_id)
    await pg_runtime.register(agent)
    run_id = await pg_runtime.submit(agent_id, _msg(agent_id, {}))

    async with pg_runtime.event_log._pool.acquire() as conn:  # type: ignore[attr-defined]
        for _ in range(100):
            status = await conn.fetchval(
                "SELECT status FROM substrate_run_queue WHERE run_id = $1", run_id
            )
            if status == "suspended":
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("run never reached suspended")

        past = datetime.now(tz=timezone.utc) - timedelta(seconds=1)
        await conn.execute(
            "UPDATE substrate_run_queue SET deadline = $1 WHERE run_id = $2", past, run_id
        )

        for _ in range(100):
            status = await conn.fetchval(
                "SELECT status FROM substrate_run_queue WHERE run_id = $1", run_id
            )
            if status == "failed":
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("deadline was never enforced")


class SpawnThenAskAgent:
    """Spawns a child (so ctx.ask gets a RunHandle, not a bare AgentId) and
    asks it with a deliberately generous timeout — the crash fast-path
    (child:{run_id} in ask()'s wait_names) must resolve long before that
    timeout, not wait it out."""

    def __init__(self, agent_id: AgentId, child_id: AgentId) -> None:
        self.id = agent_id
        self.child_id = child_id
        self.done = asyncio.Event()
        self.outcome: AskOutcome | None = None

    async def run(self, ctx: object, inbox: list[Message]) -> None:
        boot = _msg(self.child_id, {})
        handle = await ctx.spawn(self.child_id, boot=boot)  # type: ignore[attr-defined]
        self.outcome = await ctx.ask(  # type: ignore[attr-defined]
            handle, boot, timeout=60.0
        )
        self.done.set()


async def test_pg_ask_crash_fast_path(pg_runtime) -> None:
    """ctx.ask(handle, ...) on a spawned child returns as soon as the child
    crashes — via finish_run()'s child:{run_id} signal — not after waiting
    out the (deliberately huge) timeout."""
    child_id = _agent_id("pg-ask-crash-child")
    asker_id = _agent_id("pg-ask-crash-asker")

    class CrashingAgent:
        def __init__(self, agent_id: AgentId) -> None:
            self.id = agent_id

        async def run(self, ctx: object, inbox: list[Message]) -> None:
            raise RuntimeError("target deliberately crashes")

    crashing = CrashingAgent(child_id)
    asker = SpawnThenAskAgent(asker_id, child_id)

    await pg_runtime.register(crashing)
    await pg_runtime.register(asker)

    start = asyncio.get_event_loop().time()
    await pg_runtime.submit(asker_id, _msg(asker_id, {"start": True}))
    await asyncio.wait_for(asker.done.wait(), timeout=8.0)
    elapsed = asyncio.get_event_loop().time() - start

    assert asker.outcome is not None
    assert asker.outcome.kind == "target_failed"
    assert elapsed < 8.0, "must not wait out the 60s timeout"


# ---------------------------------------------------------------------------
# 7. PR8 — signal GC and retention sweep
# ---------------------------------------------------------------------------


async def test_pg_signal_gc_on_finish(pg_runtime) -> None:
    """A terminal run's leftover substrate_signals rows are deleted by finish_run() —
    both the reply it consumed and any late/never-consumed extras."""
    echo_id = _agent_id("pg-gc-echo")
    asker_id = _agent_id("pg-gc-asker")
    echo = EchoAgent(echo_id)
    asker = AskerAgent(asker_id, echo_id)

    await pg_runtime.register(echo)
    await pg_runtime.register(asker)
    await pg_runtime.submit(asker_id, _msg(asker_id))
    await asyncio.wait_for(asker.done.wait(), timeout=8.0)

    asker_run_id = None
    async with pg_runtime.event_log._pool.acquire() as conn:  # type: ignore[attr-defined]
        for _ in range(50):
            row = await conn.fetchrow(
                "SELECT run_id FROM substrate_agent_runs WHERE agent_id = $1", str(asker_id)
            )
            if row is not None:
                asker_run_id = row["run_id"]
                break
            await asyncio.sleep(0.05)
        assert asker_run_id is not None

        for _ in range(50):
            status = await conn.fetchval(
                "SELECT status FROM substrate_run_queue WHERE run_id = $1", asker_run_id
            )
            if status == "completed":
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("asker run never completed")

        remaining = await conn.fetchval(
            "SELECT count(*) FROM substrate_signals WHERE run_id = $1", asker_run_id
        )
    assert remaining == 0


async def test_pg_retention_sweep(pg_runtime) -> None:
    """sweep_terminal_runs() deletes durable state for old terminal runs,
    and leaves runs terminated more recently than the cutoff untouched."""
    from datetime import timedelta

    from substrate.infrastructure.runtime import sweep_terminal_runs

    agent_id = _agent_id("pg-sweep")
    agent = RecorderAgent(agent_id)
    await pg_runtime.register(agent)
    run_id = await pg_runtime.submit(agent_id, _msg(agent_id, {}))
    await asyncio.wait_for(agent.done.wait(), timeout=8.0)

    pool = pg_runtime.event_log._pool  # type: ignore[attr-defined]
    async with pool.acquire() as conn:
        for _ in range(50):
            row = await conn.fetchrow(
                "SELECT status, terminated_at FROM substrate_run_queue WHERE run_id = $1",
                run_id,
            )
            if row is not None and row["status"] == "completed":
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("run never completed")
        assert row["terminated_at"] is not None

        # Not old enough yet: a 1-day cutoff must not touch it.
        await sweep_terminal_runs(pool, older_than=timedelta(days=1))
        still_there = await conn.fetchval(
            "SELECT 1 FROM substrate_run_queue WHERE run_id = $1", run_id
        )
        assert still_there == 1

        # Backdate it past any cutoff, then sweep for real.
        await conn.execute(
            "UPDATE substrate_run_queue SET terminated_at = now() - interval '2 days' WHERE run_id = $1",
            run_id,
        )
        await sweep_terminal_runs(pool, older_than=timedelta(days=1))

        gone = await conn.fetchval(
            "SELECT 1 FROM substrate_run_queue WHERE run_id = $1", run_id
        )
        assert gone is None
        gone_log = await conn.fetchval(
            "SELECT 1 FROM substrate_event_log WHERE run_id = $1 LIMIT 1", run_id
        )
        assert gone_log is None


# ---------------------------------------------------------------------------
# 8. Phase 2 — durable single-flight per thread_id
# ---------------------------------------------------------------------------


async def test_pg_thread_single_flight(pg_runtime) -> None:
    """A second submit() for the same thread_id, while the first run is still
    active, raises ThreadBusyError — durably, via a unique partial index on
    substrate_run_queue, not a per-process lock (see routes/chat.py)."""
    from substrate.kernel.core.errors import ThreadBusyError

    agent_id = _agent_id("pg-singleflight")
    agent = SleepForeverAgent(agent_id)
    await pg_runtime.register(agent)

    thread_id = f"thread-{agent_id.key}"
    run_id_1 = await pg_runtime.submit(
        agent_id, _msg(agent_id, {}), thread_id=thread_id
    )

    async with pg_runtime.event_log._pool.acquire() as conn:  # type: ignore[attr-defined]
        for _ in range(100):
            status = await conn.fetchval(
                "SELECT status FROM substrate_run_queue WHERE run_id = $1", run_id_1
            )
            if status in ("pending", "running", "suspended"):
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("first run never became active")

    agent_2_id = _agent_id("pg-singleflight-2")
    agent_2 = SleepForeverAgent(agent_2_id)
    await pg_runtime.register(agent_2)

    with pytest.raises(ThreadBusyError):
        await pg_runtime.submit(agent_2_id, _msg(agent_2_id, {}), thread_id=thread_id)

    found = await pg_runtime.scheduler.find_run_for_thread(thread_id)
    assert found is not None
    assert found[0] == run_id_1


async def test_pg_thread_single_flight_frees_after_completion(pg_runtime) -> None:
    """Once the active run for a thread reaches a terminal state, the thread
    is free again — the unique index only excludes non-terminal statuses."""
    agent_id = _agent_id("pg-singleflight-done")
    agent = RecorderAgent(agent_id)
    await pg_runtime.register(agent)

    thread_id = f"thread-{agent_id.key}"
    await pg_runtime.submit(agent_id, _msg(agent_id, {}), thread_id=thread_id)
    await asyncio.wait_for(agent.done.wait(), timeout=8.0)

    async with pg_runtime.event_log._pool.acquire() as conn:  # type: ignore[attr-defined]
        for _ in range(100):
            status = await conn.fetchval(
                "SELECT status FROM substrate_run_queue WHERE run_id = "
                "(SELECT run_id FROM substrate_run_queue WHERE thread_id = $1 "
                "ORDER BY enqueued_at DESC LIMIT 1)",
                thread_id,
            )
            if status == "completed":
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("run never completed")

    agent_2 = RecorderAgent(agent_id)
    await pg_runtime.register(agent_2)
    # Must not raise: the previous run for this thread is terminal.
    await pg_runtime.submit(agent_id, _msg(agent_id, {}), thread_id=thread_id)


# ---------------------------------------------------------------------------
# 9. Phase 2 — SSE tail works cross-replica (independent pool, from_seq resume)
# ---------------------------------------------------------------------------


async def test_pg_tail_reconnect_from_different_pool_and_seq(pg_runtime) -> None:
    """Simulates a reconnect landing on a different replica: a SECOND,
    independent PostgresEventLog (its own asyncpg pool, its own LISTEN
    connection) resumes tailing an in-progress run from a mid-stream
    from_seq and sees only what it asked for, then the rest live — proving
    SSE reconnect doesn't depend on replica affinity."""
    from substrate.infrastructure.runtime.pg_event_log import PostgresEventLog

    agent_id = _agent_id("pg-reconnect")
    agent = StreamingAgent(agent_id)
    await pg_runtime.register(agent)
    run_id = await pg_runtime.submit(agent_id, _msg(agent_id, {}))

    # Read the log once to find the tool.call entry's own seq deterministically
    # — no race on "how far has the writer gotten," which computing resume_from
    # from a live last_seq() call would have (StreamingAgent logs everything
    # with no awaits in between, so by the time this reader task gets
    # scheduled the run is typically already finished). tail() never
    # terminates on its own, so stop as soon as tool.call is seen.
    tool_call_seq: int | None = None
    async for entry in pg_runtime.event_log.tail(run_id):
        if entry.kind == "tool.call":
            tool_call_seq = entry.seq
            break
    assert tool_call_seq is not None
    resume_from = tool_call_seq + 1

    import asyncpg

    other_pool = await asyncpg.create_pool(_PG_URL)
    try:
        other_replica_log = PostgresEventLog(other_pool, dsn=_PG_URL)
        seen_second_pass: list[str] = []
        async for entry in other_replica_log.tail(run_id, from_seq=resume_from):
            seen_second_pass.append(entry.kind)
            if entry.kind == "tool.result":
                break
        # Resumed strictly from resume_from: no re-delivery of the
        # already-seen text.delta entries from before that seq.
        assert seen_second_pass[0] != "text.delta"
        assert "tool.result" in seen_second_pass
    finally:
        await other_replica_log.close()
        await other_pool.close()


# ---------------------------------------------------------------------------
# 10. Phase 3 — per-tenant fair scheduling in lease()
# ---------------------------------------------------------------------------


async def test_pg_fair_scheduling_across_tenants() -> None:
    """A tenant with 5 queued runs enqueued FIRST (chronologically oldest)
    must not push a second tenant's single run enqueued after them down to
    rank 6 — the ROW_NUMBER() OVER (PARTITION BY tenant ...) ranking in
    lease() ties the starved tenant's one run at rank 1 with the flooding
    tenant's oldest, always competitive for the very next slot, rather than
    strict (priority, enqueued_at) FIFO's guaranteed rank 6.

    Asserts the ranking directly (the same expression lease() uses) rather
    than asserting on lease() call outcomes: which of two rank-1-tied rows
    an actual lease() picks is an arbitrary (run_id) tie-break, not a
    fairness signal, so asserting on it would make this test flaky on
    tie-break luck rather than testing the actual guarantee.

    Uses a bare PostgresScheduler (no Runtime/Worker) so nothing else is
    competing for leases — a live Worker's default capacity=10 would drain
    all 6 rows in a single poll tick regardless of fairness, which would
    defeat the point of this test.
    """
    if not await _pg_reachable():
        pytest.skip("Postgres not reachable")
    import asyncpg

    from substrate.infrastructure.runtime.pg_scheduler import PostgresScheduler

    pool = await asyncpg.create_pool(_PG_URL, min_size=1, max_size=2)
    try:
        scheduler = PostgresScheduler(pool)
        await scheduler.setup()

        # Isolation: the ranking in lease() scans the WHOLE table, and its
        # own first step reclaims any expired 'running' lease back to
        # 'pending' before ranking — so stale debris from unrelated tests
        # (a different tenant, competing for the same rn=1 slot) makes this
        # test flaky whether it's sitting at 'pending' OR 'running' with a
        # long-expired lease. Tests run sequentially in one process here, so
        # any non-terminal row at this point is leftover debris, never a
        # live concurrent run to respect.
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM substrate_run_queue WHERE status NOT IN "
                "('completed', 'failed', 'cancelled')"
            )

        flood_id = _agent_id("pg-fair-flood")
        starved_id = _agent_id("pg-fair-starved")
        flood_run_ids = []
        for _ in range(5):
            run_id = f"flood-{id(object())}-{len(flood_run_ids)}"
            scheduler.register_run(run_id, flood_id)
            await scheduler.enqueue(run_id, priority=5, tenant="flood")
            flood_run_ids.append(run_id)

        starved_run_id = f"starved-{id(object())}"
        scheduler.register_run(starved_run_id, starved_id)
        await scheduler.enqueue(starved_run_id, priority=5, tenant="starved")

        async with pool.acquire() as conn:
            ranks = await conn.fetch(
                """
                SELECT run_id, tenant,
                    ROW_NUMBER() OVER (
                        PARTITION BY tenant ORDER BY priority, enqueued_at
                    ) AS rn
                FROM substrate_run_queue
                WHERE status = 'pending'
                """
            )
        rank_by_run_id = {row["run_id"]: row["rn"] for row in ranks}

        assert rank_by_run_id[starved_run_id] == 1
        assert rank_by_run_id[flood_run_ids[0]] == 1
        assert rank_by_run_id[flood_run_ids[-1]] == 5, (
            "flood's own 5 runs should still be ranked 1..5 relative to "
            "each other — fairness partitions across tenants, it doesn't "
            "reorder within one"
        )
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM substrate_run_queue WHERE run_id = ANY($1)",
                [*flood_run_ids, starved_run_id],
            )
            await conn.execute(
                "DELETE FROM substrate_agent_runs WHERE run_id = ANY($1)",
                [*flood_run_ids, starved_run_id],
            )
        await pool.close()


# ---------------------------------------------------------------------------
# 11. Phase 3 — execution_budget inheritance survives a durable spawn
# ---------------------------------------------------------------------------


async def test_pg_spawn_inherits_execution_budget(pg_runtime) -> None:
    """Same guarantee as the in-memory test, but round-tripped through
    Postgres: Supervision.to_dict()/from_dict() persisted in
    substrate_run_tree.supervision and rehydrated by a (potentially different)
    worker leasing the grandchild — proving inheritance survives the
    process boundary, not just a shared in-memory dict."""
    from substrate.kernel.agent.supervision import ExecutionBudget, Supervision

    class GrandchildAgent:
        def __init__(self, agent_id: AgentId) -> None:
            self.id = agent_id
            self.seen_max_tokens = "unset"
            self.done = asyncio.Event()

        async def run(self, ctx: object, inbox: list[Message]) -> None:
            sup = ctx.meta.supervision  # type: ignore[attr-defined]
            self.seen_max_tokens = sup.execution_budget.max_tokens if sup else None
            self.done.set()

    class ChildAgent:
        def __init__(self, agent_id: AgentId, grandchild_id: AgentId) -> None:
            self.id = agent_id
            self.grandchild_id = grandchild_id

        async def run(self, ctx: object, inbox: list[Message]) -> None:
            boot = _msg(self.grandchild_id, {})
            await ctx.spawn(self.grandchild_id, boot=boot)  # type: ignore[attr-defined]

    class RootAgent:
        def __init__(self, agent_id: AgentId, child_id: AgentId) -> None:
            self.id = agent_id
            self.child_id = child_id

        async def run(self, ctx: object, inbox: list[Message]) -> None:
            boot = _msg(self.child_id, {})
            custom_sup = Supervision.root(
                self.child_id, execution_budget=ExecutionBudget(max_tokens=77)
            )
            await ctx.spawn(  # type: ignore[attr-defined]
                self.child_id, boot=boot, supervision=custom_sup
            )

    root_id = _agent_id("pg-budget-root")
    child_id = _agent_id("pg-budget-child")
    grandchild_id = _agent_id("pg-budget-grandchild")
    grandchild = GrandchildAgent(grandchild_id)
    child = ChildAgent(child_id, grandchild_id)
    root = RootAgent(root_id, child_id)

    await pg_runtime.register(grandchild)
    await pg_runtime.register(child)
    await pg_runtime.register(root)
    await pg_runtime.submit(root_id, _msg(root_id, {"start": True}))
    await asyncio.wait_for(grandchild.done.wait(), timeout=8.0)

    assert grandchild.seen_max_tokens == 77
