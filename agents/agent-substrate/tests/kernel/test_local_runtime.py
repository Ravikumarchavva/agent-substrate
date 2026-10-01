"""Tests for the SQLite no-infra-durable runtime backends (agents/runtime/backends/_local_*.py).

Each backend must satisfy the exact same kernel Protocol contract as its
in-memory sibling. The property that actually matters and the in-memory
tier structurally cannot offer: durability across a process restart —
covered here by closing one ``LocalRuntimeDB``/backend instance and opening
a fresh one against the same file.
"""

from __future__ import annotations

from substrate.kernel.llm import ModelCapabilities
from pathlib import Path

import pytest

from substrate.kernel.agent.supervision import Supervision
from substrate.kernel.core.content import ChatMessage, Role, TextBlock
from substrate.kernel.core.identity import Actor, Topic
from substrate.kernel.exceptions import ConcurrentAppendError, ThreadBusyError
from substrate.kernel.messaging.message import ChatPayload, Message
from substrate.kernel.runtime.ids import RunStatus, new_run_id
from substrate.kernel.runtime.log_entry import RunLogEntry
from substrate.kernel.runtime.wakeup import Wakeup

from substrate.agents.runtime.backends._local_db import LocalRuntimeDB
from substrate.agents.runtime.backends._local_event_log import LocalEventLog
from substrate.agents.runtime.backends._local_follow_graph import LocalFollowGraph
from substrate.agents.runtime.backends._local_inbox import LocalInbox
from substrate.agents.runtime.backends._local_scheduler import LocalScheduler
from substrate.agents.runtime.backends._local_signal_bus import LocalSignalBus
from substrate.agents.runtime.backends._local_supervisor import LocalSupervisor
from substrate.agents.runtime.local_runtime import build_local_runtime


def _msg(text: str, *, target: Actor, sender: Actor) -> Message:
    return Message(
        target=target,
        sender=sender,
        payload=ChatPayload(message=ChatMessage(role=Role.USER, content=[TextBlock(text=text)])),
    )


# ── EventLog ─────────────────────────────────────────────────────────────


async def test_event_log_append_read_and_seq_conflict(tmp_path: Path) -> None:
    db = LocalRuntimeDB(tmp_path / "rt.db")
    log = LocalEventLog(db)
    run_id = new_run_id()

    seq0 = await log.append(run_id, RunLogEntry(run_id=run_id, seq=0, kind="a", payload={}), expected_seq=-1)
    assert seq0 == 0
    seq1 = await log.append(run_id, RunLogEntry(run_id=run_id, seq=1, kind="b", payload={}), expected_seq=0)
    assert seq1 == 1

    with pytest.raises(ConcurrentAppendError):
        await log.append(run_id, RunLogEntry(run_id=run_id, seq=1, kind="c", payload={}), expected_seq=0)

    entries = [e async for e in log.read(run_id)]
    assert [e.kind for e in entries] == ["a", "b"]
    assert await log.last_seq(run_id) == 1
    db.close()


async def test_event_log_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "rt.db"
    run_id = new_run_id()

    db1 = LocalRuntimeDB(path)
    log1 = LocalEventLog(db1)
    await log1.append(run_id, RunLogEntry(run_id=run_id, seq=0, kind="a", payload={"x": 1}), expected_seq=-1)
    db1.close()

    db2 = LocalRuntimeDB(path)
    log2 = LocalEventLog(db2)
    entries = [e async for e in log2.read(run_id)]
    assert len(entries) == 1
    assert entries[0].payload == {"x": 1}
    db2.close()


# ── Inbox ────────────────────────────────────────────────────────────────


async def test_inbox_deliver_dedup_drain_ack(tmp_path: Path) -> None:
    db = LocalRuntimeDB(tmp_path / "rt.db")
    inbox = LocalInbox(db)
    agent = Actor(type="a", key="1")
    sender = Actor(type="proxy", key="user")
    msg = _msg("hi", target=agent, sender=sender)

    assert await inbox.deliver(agent, msg) is True
    assert await inbox.deliver(agent, msg) is False  # dedup by id
    assert await inbox.pending_count(agent) == 1

    drained = await inbox.drain(agent)
    assert len(drained) == 1 and drained[0].id == msg.id

    await inbox.ack(agent, msg.id)
    assert await inbox.pending_count(agent) == 0
    db.close()


async def test_inbox_nack_dead_letters_after_max_retries(tmp_path: Path) -> None:
    db = LocalRuntimeDB(tmp_path / "rt.db")
    inbox = LocalInbox(db, max_retries=2)
    agent = Actor(type="a", key="1")
    msg = _msg("hi", target=agent, sender=Actor(type="proxy", key="user"))
    await inbox.deliver(agent, msg)

    await inbox.nack(agent, msg.id, error="boom1")
    assert await inbox.pending_count(agent) == 1  # not dead yet
    await inbox.nack(agent, msg.id, error="boom2")
    assert await inbox.pending_count(agent) == 0  # dead-lettered + acked

    dead = await inbox.dead_letters(agent)
    assert len(dead) == 1
    assert dead[0].attempts == 2
    assert dead[0].last_error == "boom2"
    db.close()


# ── FollowGraph ──────────────────────────────────────────────────────────


async def test_follow_graph_follow_unfollow(tmp_path: Path) -> None:
    db = LocalRuntimeDB(tmp_path / "rt.db")
    graph = LocalFollowGraph(db)
    agent = Actor(type="a", key="1")
    topic = Topic("news")

    sub = await graph.follow(agent, topic)
    assert [a async for a in graph.followers_of(topic)] == [agent]
    assert [t.name async for t in graph.following(agent)] == ["news"]

    await graph.unfollow(sub)
    assert [a async for a in graph.followers_of(topic)] == []
    db.close()


# ── Scheduler ────────────────────────────────────────────────────────────


async def test_scheduler_enqueue_lease_release_round_trip(tmp_path: Path) -> None:
    db = LocalRuntimeDB(tmp_path / "rt.db")
    sched = LocalScheduler(db)
    run_id = new_run_id()
    agent = Actor(type="a", key="1")
    sched.register_run(run_id, agent)
    assert sched.agent_for(run_id) == agent

    await sched.enqueue(run_id, tenant="t1")
    assert await sched.get_status(run_id) == RunStatus.PENDING

    leases = await sched.lease(worker_id="w1", capacity=5)
    assert len(leases) == 1 and leases[0].run_id == run_id
    assert await sched.get_status(run_id) == RunStatus.RUNNING

    ok = await sched.release(leases[0], status=RunStatus.COMPLETED)
    assert ok is True
    assert await sched.get_status(run_id) == RunStatus.COMPLETED
    db.close()


async def test_scheduler_thread_singleflight_raises(tmp_path: Path) -> None:
    db = LocalRuntimeDB(tmp_path / "rt.db")
    sched = LocalScheduler(db)
    r1, r2 = new_run_id(), new_run_id()
    sched.register_run(r1, Actor(type="a", key="1"))
    sched.register_run(r2, Actor(type="a", key="2"))

    await sched.enqueue(r1, tenant="t1", thread_id="thread-x")
    with pytest.raises(ThreadBusyError):
        await sched.enqueue(r2, tenant="t1", thread_id="thread-x")
    db.close()


async def test_scheduler_retry_backoff_then_wake_via_lease_poll(tmp_path: Path) -> None:
    from substrate.kernel.runtime.scheduler import RunRetryPolicy

    db = LocalRuntimeDB(tmp_path / "rt.db")
    sched = LocalScheduler(db)
    run_id = new_run_id()
    sched.register_run(run_id, Actor(type="a", key="1"))
    await sched.enqueue(
        run_id, tenant="t1", retry_policy=RunRetryPolicy(max_retries=1, backoff_s=0)
    )
    leases = await sched.lease(worker_id="w1", capacity=1)
    ok = await sched.release(leases[0], status=RunStatus.FAILED)
    assert ok is False  # parked SUSPENDED for retry, not terminal yet
    assert await sched.get_status(run_id) == RunStatus.SUSPENDED

    # backoff_s=0 → wake_at is already due; next lease() poll reclaims it.
    leases2 = await sched.lease(worker_id="w1", capacity=1)
    assert len(leases2) == 1 and leases2[0].run_id == run_id
    db.close()


async def test_scheduler_state_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "rt.db"
    run_id = new_run_id()
    agent = Actor(type="a", key="1")

    db1 = LocalRuntimeDB(path)
    sched1 = LocalScheduler(db1)
    sched1.register_run(run_id, agent)
    await sched1.enqueue(run_id, tenant="t1")
    db1.close()

    db2 = LocalRuntimeDB(path)
    sched2 = LocalScheduler(db2)
    assert await sched2.get_status(run_id) == RunStatus.PENDING
    assert sched2.agent_for(run_id) == agent
    db2.close()


# ── SignalBus ────────────────────────────────────────────────────────────


async def test_signal_bus_signal_consume_idempotent(tmp_path: Path) -> None:
    db = LocalRuntimeDB(tmp_path / "rt.db")
    sched = LocalScheduler(db)
    bus = LocalSignalBus(db, sched)
    run_id = new_run_id()

    await bus.signal(run_id, "reply", {"text": "hi"})
    payload = await bus.consume(run_id, "reply", "effect-1")
    assert payload == {"text": "hi"}
    # Idempotent re-claim on replay: same effect_id returns the same payload
    # without needing a second buffered entry.
    again = await bus.consume(run_id, "reply", "effect-1")
    assert again == {"text": "hi"}
    db.close()


async def test_signal_bus_wakes_suspended_run_waiting_on_that_signal(tmp_path: Path) -> None:
    db = LocalRuntimeDB(tmp_path / "rt.db")
    sched = LocalScheduler(db)
    bus = LocalSignalBus(db, sched)
    run_id = new_run_id()
    sched.register_run(run_id, Actor(type="a", key="1"))
    await sched.enqueue(run_id, tenant="t1")
    leases = await sched.lease(worker_id="w1", capacity=1)
    await sched.release(
        leases[0], status=RunStatus.SUSPENDED, wake_on=Wakeup(kind="signal", signals=["go"])
    )
    assert await sched.get_status(run_id) == RunStatus.SUSPENDED

    await bus.signal(run_id, "go", {})
    assert await sched.get_status(run_id) == RunStatus.PENDING
    db.close()


# ── Supervisor + full Runtime integration ───────────────────────────────


async def test_supervisor_spawn_is_idempotent_by_effect_id(tmp_path: Path) -> None:
    db = LocalRuntimeDB(tmp_path / "rt.db")
    sched = LocalScheduler(db)
    log = LocalEventLog(db)
    inbox = LocalInbox(db)
    bus = LocalSignalBus(db, sched)
    sup = LocalSupervisor(db, event_log=log, inbox=inbox, scheduler=sched, signal_bus=bus)

    parent = new_run_id()
    sched.register_run(parent, Actor(type="orchestrator", key="1"))
    await log.append(parent, RunLogEntry(run_id=parent, seq=0, kind="run.started", payload={}), expected_seq=-1)
    supervision = Supervision.root(Actor(type="orchestrator", key="1"))
    child_agent = Actor(type="worker", key="1")
    boot = _msg("task", target=child_agent, sender=Actor(type="orchestrator", key="1"))

    h1 = await sup.spawn(
        child_agent, parent=parent, supervision=supervision, boot=boot, path="p0", correlation_id="c1"
    )
    h2 = await sup.spawn(
        child_agent, parent=parent, supervision=supervision, boot=boot, path="p0", correlation_id="c1"
    )
    assert h1.run_id == h2.run_id  # replay returns the same child

    await sup.finish_run(h1.run_id, RunStatus.COMPLETED)
    result = await sup._get_result(h1.run_id)
    assert result is not None and result.status == RunStatus.COMPLETED
    db.close()


async def test_supervisor_cancel_cascades_to_children(tmp_path: Path) -> None:
    db = LocalRuntimeDB(tmp_path / "rt.db")
    sched = LocalScheduler(db)
    log = LocalEventLog(db)
    inbox = LocalInbox(db)
    bus = LocalSignalBus(db, sched)
    sup = LocalSupervisor(db, event_log=log, inbox=inbox, scheduler=sched, signal_bus=bus)

    parent = new_run_id()
    sched.register_run(parent, Actor(type="orchestrator", key="1"))
    await log.append(parent, RunLogEntry(run_id=parent, seq=0, kind="run.started", payload={}), expected_seq=-1)
    supervision = Supervision.root(Actor(type="orchestrator", key="1"))
    child_agent = Actor(type="worker", key="1")
    boot = _msg("task", target=child_agent, sender=Actor(type="orchestrator", key="1"))

    handle = await sup.spawn(
        child_agent, parent=parent, supervision=supervision, boot=boot, path="p0", correlation_id="c1"
    )
    await sup.cancel(handle)
    assert await sched.get_status(handle.run_id) == RunStatus.CANCELLED
    db.close()


async def test_react_agent_runs_end_to_end_on_local_runtime(tmp_path: Path) -> None:
    """The whole stack together via the Runtime facade — a real agent turn,
    same shape as tests/agents/test_budgets.py's in-memory equivalent, just
    on build_local_runtime()."""
    from substrate.agents.core.react import ReActAgent
    from substrate.kernel.core.usage import Usage
    from substrate.kernel.messaging.stream import CompletionEvent

    class MockLLMClient:
        model = "mock-model"
        capabilities = ModelCapabilities(model_id="mock-model")

        async def generate_stream(self, messages, *, options=None, ctx=None):
            yield CompletionEvent(
                content=[TextBlock(text="hello from local runtime")],
                usage=Usage(input_tokens=10, output_tokens=5),
            )

    agent = ReActAgent("LocalBot", model=MockLLMClient(), max_iterations=3)

    async with build_local_runtime(path=tmp_path / "rt.db") as rt:
        await rt.register(agent)
        msg = Message(
            target=agent.id,
            sender=Actor(type="proxy", key="user"),
            payload=ChatPayload(message=ChatMessage(role=Role.USER, content=[TextBlock(text="hi")])),
        )
        run_id = await rt.submit(agent.id, msg)
        async for entry in rt.event_log.tail(run_id):
            if entry.kind in ("run.completed", "run.failed", "run.cancelled"):
                assert entry.kind == "run.completed"
                break
