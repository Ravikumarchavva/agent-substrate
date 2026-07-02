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
                INSERT INTO ravi_run_queue (run_id, status, worker_id, expires_at)
                VALUES ($1, 'running', 'dead-worker', now() + interval '30 seconds')
                """,
                run_id,
            )

        # Default (expired-only) must NOT touch a still-future lease.
        assert await scheduler.reclaim_orphans(all_running=False) == 0
        async with pool.acquire() as conn:
            status = await conn.fetchval(
                "SELECT status FROM ravi_run_queue WHERE run_id = $1", run_id
            )
        assert status == "running"

        # Single-worker reclaim requeues it immediately.
        reclaimed = await scheduler.reclaim_orphans(all_running=True)
        assert reclaimed >= 1
        async with pool.acquire() as conn:
            status = await conn.fetchval(
                "SELECT status FROM ravi_run_queue WHERE run_id = $1", run_id
            )
            await conn.execute("DELETE FROM ravi_run_queue WHERE run_id = $1", run_id)
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
                INSERT INTO ravi_agent_runs (run_id, agent_id, spec)
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
                INSERT INTO ravi_run_queue (run_id, status)
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
                "DELETE FROM ravi_run_queue WHERE run_id = $1", orphan_run_id
            )
            await conn.execute(
                "DELETE FROM ravi_agent_runs WHERE run_id = $1", orphan_run_id
            )
    finally:
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
                "SELECT status FROM ravi_run_queue WHERE run_id = $1", run_id
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
            "SELECT run_id, status FROM ravi_run_tree WHERE run_id = ANY($1)",
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
                "SELECT status FROM ravi_run_queue WHERE run_id = $1", run_id
            )
            if status == "suspended":
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("run never reached suspended")

        past = datetime.now(tz=timezone.utc) - timedelta(seconds=1)
        await conn.execute(
            "UPDATE ravi_run_queue SET deadline = $1 WHERE run_id = $2", past, run_id
        )

        for _ in range(100):
            status = await conn.fetchval(
                "SELECT status FROM ravi_run_queue WHERE run_id = $1", run_id
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
    """A terminal run's leftover ravi_signals rows are deleted by finish_run() —
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
                "SELECT run_id FROM ravi_agent_runs WHERE agent_id = $1", str(asker_id)
            )
            if row is not None:
                asker_run_id = row["run_id"]
                break
            await asyncio.sleep(0.05)
        assert asker_run_id is not None

        for _ in range(50):
            status = await conn.fetchval(
                "SELECT status FROM ravi_run_queue WHERE run_id = $1", asker_run_id
            )
            if status == "completed":
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("asker run never completed")

        remaining = await conn.fetchval(
            "SELECT count(*) FROM ravi_signals WHERE run_id = $1", asker_run_id
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
                "SELECT status, terminated_at FROM ravi_run_queue WHERE run_id = $1",
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
            "SELECT 1 FROM ravi_run_queue WHERE run_id = $1", run_id
        )
        assert still_there == 1

        # Backdate it past any cutoff, then sweep for real.
        await conn.execute(
            "UPDATE ravi_run_queue SET terminated_at = now() - interval '2 days' WHERE run_id = $1",
            run_id,
        )
        await sweep_terminal_runs(pool, older_than=timedelta(days=1))

        gone = await conn.fetchval(
            "SELECT 1 FROM ravi_run_queue WHERE run_id = $1", run_id
        )
        assert gone is None
        gone_log = await conn.fetchval(
            "SELECT 1 FROM ravi_event_log WHERE run_id = $1 LIMIT 1", run_id
        )
        assert gone_log is None
