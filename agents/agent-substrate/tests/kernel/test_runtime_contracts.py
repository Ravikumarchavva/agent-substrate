"""Conformance tests for the durable runtime kernel contracts.

These tests verify that:
1. All value types (RunLogEntry, Effect, Wakeup, …) round-trip through JSON.
2. All Protocol interfaces are structurally sound (implementable by a minimal stub).
3. Effect.make_id is deterministic and collision-resistant.
4. RunContext carries run_id correctly in both standalone and supervised modes.
5. Error types carry their structured fields.

When Stage 1 (Postgres) in-memory impls are added, the same suite is run
against those implementations unchanged — that is the "swap the impl, not the
contract" verification the plan promises.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import AsyncIterator
from uuid import uuid4

import pytest

from ravi.kernel.core.identity import AgentId, TopicId
from ravi.kernel.messaging.message import Message, DataPayload
from ravi.kernel.core.errors import ConcurrentAppendError, SpawnDenied
from ravi.kernel.agent.runtime_context import CancellationToken, RunMeta
from ravi.kernel.agent.supervision import Supervision
from ravi.kernel.runtime.ids import RunId, RunStatus, new_run_id
from ravi.kernel.runtime.log_entry import RunLogEntry
from ravi.kernel.runtime.effects import Effect, EffectResult
from ravi.kernel.runtime.inbox import DeadLetterReason, DeadLetterEntry
from ravi.kernel.runtime.wakeup import Wakeup
from ravi.kernel.runtime.scheduler import Lease, RunRetryPolicy
from ravi.kernel.runtime.supervisor import RunHandle, RunResult
from ravi.kernel.runtime.agent import AgentRunContext, Agent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agent_id(name: str = "test") -> AgentId:
    return AgentId(type=name, key=uuid4().hex)


def _topic() -> TopicId:
    return TopicId(type="test.topic", source=uuid4().hex)


def _message(sender: AgentId | None = None, target: AgentId | None = None) -> Message:
    target = target or _agent_id()
    return Message(
        target=target,
        payload=DataPayload(data={"x": 1}),
        sender=sender,
    )


# ---------------------------------------------------------------------------
# ids.py
# ---------------------------------------------------------------------------


class TestRunId:
    def test_new_run_id_is_str(self) -> None:
        rid = new_run_id()
        assert isinstance(rid, str)
        assert len(rid) > 0

    def test_new_run_id_unique(self) -> None:
        assert new_run_id() != new_run_id()

    def test_run_status_values(self) -> None:
        statuses = {s.value for s in RunStatus}
        assert statuses == {
            "pending",
            "running",
            "suspended",
            "completed",
            "failed",
            "cancelled",
        }

    def test_run_status_terminal(self) -> None:
        terminal = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
        non_terminal = {RunStatus.PENDING, RunStatus.RUNNING, RunStatus.SUSPENDED}
        assert terminal | non_terminal == set(RunStatus)


# ---------------------------------------------------------------------------
# log_entry.py
# ---------------------------------------------------------------------------


class TestRunLogEntry:
    def test_round_trip_json(self) -> None:
        entry = RunLogEntry(
            run_id=new_run_id(),
            seq=0,
            kind="run.started",
            payload={"boot": "hello"},
        )
        restored = RunLogEntry.model_validate_json(entry.model_dump_json())
        assert restored == entry

    def test_frozen(self) -> None:
        entry = RunLogEntry(run_id=new_run_id(), seq=0, kind="msg.received")
        with pytest.raises((TypeError, Exception)):
            entry.seq = 99  # type: ignore[misc]

    def test_default_payload_empty(self) -> None:
        entry = RunLogEntry(run_id=new_run_id(), seq=0, kind="test")
        assert entry.payload == {}

    def test_ts_auto_populated(self) -> None:
        entry = RunLogEntry(run_id=new_run_id(), seq=0, kind="test")
        assert entry.ts is not None
        assert entry.ts.tzinfo is not None


class InMemoryEventLog:
    """Minimal in-memory EventLog for contract conformance testing."""

    def __init__(self) -> None:
        self._logs: dict[RunId, list[RunLogEntry]] = defaultdict(list)
        self._waiters: dict[RunId, list[asyncio.Event]] = defaultdict(list)

    async def append(
        self, run_id: RunId, entry: RunLogEntry, *, expected_seq: int
    ) -> int:
        current = len(self._logs[run_id]) - 1
        if current != expected_seq:
            raise ConcurrentAppendError(
                f"expected {expected_seq}, got {current}",
                run_id=run_id,
                expected_seq=expected_seq,
                actual_seq=current,
            )
        self._logs[run_id].append(entry)
        new_seq = len(self._logs[run_id]) - 1
        for ev in self._waiters[run_id]:
            ev.set()
        self._waiters[run_id].clear()
        return new_seq

    def read(self, run_id: RunId, *, from_seq: int = 0) -> AsyncIterator[RunLogEntry]:
        return self._read_iter(run_id, from_seq)

    async def _read_iter(self, run_id: RunId, from_seq: int):  # type: ignore[return]
        for entry in self._logs[run_id][from_seq:]:
            yield entry

    def tail(self, run_id: RunId, *, from_seq: int = 0) -> AsyncIterator[RunLogEntry]:
        return self._tail_iter(run_id, from_seq)

    async def _tail_iter(self, run_id: RunId, from_seq: int):  # type: ignore[return]
        idx = from_seq
        while True:
            entries = self._logs[run_id]
            while idx < len(entries):
                yield entries[idx]
                idx += 1
            ev = asyncio.Event()
            self._waiters[run_id].append(ev)
            await ev.wait()

    async def last_seq(self, run_id: RunId) -> int:
        return len(self._logs[run_id]) - 1


class TestEventLog:
    async def test_append_and_read(self) -> None:
        log = InMemoryEventLog()
        rid = new_run_id()
        e0 = RunLogEntry(run_id=rid, seq=0, kind="run.started")
        e1 = RunLogEntry(run_id=rid, seq=1, kind="msg.received")

        await log.append(rid, e0, expected_seq=-1)
        await log.append(rid, e1, expected_seq=0)

        entries = [e async for e in log.read(rid)]
        assert len(entries) == 2
        assert entries[0].kind == "run.started"
        assert entries[1].kind == "msg.received"

    async def test_concurrent_append_raises(self) -> None:
        log = InMemoryEventLog()
        rid = new_run_id()
        e0 = RunLogEntry(run_id=rid, seq=0, kind="run.started")
        await log.append(rid, e0, expected_seq=-1)

        with pytest.raises(ConcurrentAppendError) as exc:
            await log.append(
                rid, RunLogEntry(run_id=rid, seq=1, kind="x"), expected_seq=-1
            )
        assert exc.value.run_id == rid
        assert exc.value.expected_seq == -1
        assert exc.value.actual_seq == 0

    async def test_last_seq_empty(self) -> None:
        log = InMemoryEventLog()
        assert await log.last_seq(new_run_id()) == -1

    async def test_read_from_seq(self) -> None:
        log = InMemoryEventLog()
        rid = new_run_id()
        for i in range(5):
            await log.append(
                rid, RunLogEntry(run_id=rid, seq=i, kind=f"k{i}"), expected_seq=i - 1
            )
        entries = [e async for e in log.read(rid, from_seq=3)]
        assert [e.kind for e in entries] == ["k3", "k4"]

    async def test_tail_yields_existing_then_new(self) -> None:
        log = InMemoryEventLog()
        rid = new_run_id()
        await log.append(
            rid, RunLogEntry(run_id=rid, seq=0, kind="k0"), expected_seq=-1
        )

        collected: list[str] = []

        async def consume() -> None:
            async for entry in log.tail(rid):
                collected.append(entry.kind)
                if len(collected) >= 2:
                    break

        async def produce() -> None:
            await asyncio.sleep(0.01)
            await log.append(
                rid, RunLogEntry(run_id=rid, seq=1, kind="k1"), expected_seq=0
            )

        await asyncio.gather(consume(), produce())
        assert collected == ["k0", "k1"]


# ---------------------------------------------------------------------------
# effects.py
# ---------------------------------------------------------------------------


class TestEffect:
    def test_make_id_deterministic(self) -> None:
        rid = new_run_id()
        args = {"email": "a@b.com", "subject": "hi"}
        id1 = Effect.make_id(rid, 3, "email.send", args)
        id2 = Effect.make_id(rid, 3, "email.send", args)
        assert id1 == id2

    def test_make_id_arg_order_independent(self) -> None:
        rid = new_run_id()
        id1 = Effect.make_id(rid, 0, "k", {"a": 1, "b": 2})
        id2 = Effect.make_id(rid, 0, "k", {"b": 2, "a": 1})
        assert id1 == id2

    def test_make_id_different_steps_differ(self) -> None:
        rid = new_run_id()
        assert Effect.make_id(rid, 0, "k", {}) != Effect.make_id(rid, 1, "k", {})

    def test_round_trip_json(self) -> None:
        e = Effect(id="abc123", kind="email.send", spec={"to": "x@y.com"})
        restored = Effect.model_validate_json(e.model_dump_json())
        assert restored == e

    def test_frozen(self) -> None:
        e = Effect(id="x", kind="k")
        with pytest.raises((TypeError, Exception)):
            e.kind = "y"  # type: ignore[misc]


class InMemoryJournal:
    def __init__(self) -> None:
        self._store: dict[str, EffectResult] = {}

    async def lookup(self, effect_id: str) -> EffectResult | None:
        return self._store.get(effect_id)

    async def record(self, result: EffectResult) -> None:
        self._store.setdefault(result.effect_id, result)  # write-once


class TestJournal:
    async def test_lookup_miss_returns_none(self) -> None:
        j = InMemoryJournal()
        assert await j.lookup("nonexistent") is None

    async def test_record_then_lookup(self) -> None:
        j = InMemoryJournal()
        result = EffectResult(effect_id="eid1", status="ok", value={"sent": True})
        await j.record(result)
        hit = await j.lookup("eid1")
        assert hit is not None
        assert hit.status == "ok"

    async def test_record_write_once(self) -> None:
        j = InMemoryJournal()
        r1 = EffectResult(effect_id="eid2", status="ok", value={"v": 1})
        r2 = EffectResult(effect_id="eid2", status="error", value={"v": 2})
        await j.record(r1)
        await j.record(r2)
        hit = await j.lookup("eid2")
        assert hit is not None
        assert hit.status == "ok"  # first wins

    async def test_result_round_trip_json(self) -> None:
        r = EffectResult(effect_id="x", status="error", value={"err": "timeout"})
        restored = EffectResult.model_validate_json(r.model_dump_json())
        assert restored == r


# ---------------------------------------------------------------------------
# inbox.py
# ---------------------------------------------------------------------------


class InMemoryInbox:
    """Minimal in-memory Inbox — dedup + FIFO + dead-letter after 3 retries."""

    MAX_RETRIES = 3

    def __init__(self) -> None:
        self._queues: dict[str, deque[Message]] = defaultdict(deque)
        self._seen: dict[str, set[str]] = defaultdict(set)  # agent_id -> msg_ids
        self._retries: dict[str, dict[str, int]] = defaultdict(dict)
        self._dead: dict[str, list[DeadLetterEntry]] = defaultdict(list)

    def _key(self, agent_id: AgentId) -> str:
        return str(agent_id)

    async def deliver(self, agent_id: AgentId, msg: Message) -> bool:
        k = self._key(agent_id)
        if msg.id in self._seen[k]:
            return False
        self._seen[k].add(msg.id)
        self._queues[k].append(msg)
        return True

    async def drain(self, agent_id: AgentId, *, max: int = 100) -> list[Message]:
        k = self._key(agent_id)
        q = self._queues[k]
        result = []
        for _ in range(min(max, len(q))):
            result.append(q[0])
            q.rotate(-1)  # move to back (not yet acked)
        return result

    async def ack(self, agent_id: AgentId, msg_id: str) -> None:
        k = self._key(agent_id)
        q = self._queues[k]
        self._queues[k] = deque(m for m in q if m.id != msg_id)
        self._retries[k].pop(msg_id, None)

    async def nack(self, agent_id: AgentId, msg_id: str, *, error: str = "") -> None:
        k = self._key(agent_id)
        count = self._retries[k].get(msg_id, 0) + 1
        self._retries[k][msg_id] = count
        if count >= self.MAX_RETRIES:
            msg_list = [m for m in self._queues[k] if m.id == msg_id]
            self._queues[k] = deque(m for m in self._queues[k] if m.id != msg_id)
            if msg_list:
                self._dead[k].append(
                    DeadLetterEntry(
                        agent_id=agent_id,
                        msg=msg_list[0],
                        reason=DeadLetterReason.MAX_RETRIES,
                        attempts=count,
                        last_error=error or None,
                    )
                )

    async def dead_letters(self, agent_id: AgentId) -> list[DeadLetterEntry]:
        return list(self._dead[self._key(agent_id)])

    async def pending_count(self, agent_id: AgentId) -> int:
        return len(self._queues[self._key(agent_id)])


class TestInbox:
    async def test_deliver_and_drain(self) -> None:
        inbox = InMemoryInbox()
        agent = _agent_id()
        msg = _message(target=agent)
        delivered = await inbox.deliver(agent, msg)
        assert delivered is True
        msgs = await inbox.drain(agent)
        assert len(msgs) == 1
        assert msgs[0].id == msg.id

    async def test_dedup_idempotent(self) -> None:
        inbox = InMemoryInbox()
        agent = _agent_id()
        msg = _message(target=agent)
        r1 = await inbox.deliver(agent, msg)
        r2 = await inbox.deliver(agent, msg)
        assert r1 is True
        assert r2 is False
        assert await inbox.pending_count(agent) == 1

    async def test_ack_removes_message(self) -> None:
        inbox = InMemoryInbox()
        agent = _agent_id()
        msg = _message(target=agent)
        await inbox.deliver(agent, msg)
        await inbox.ack(agent, msg.id)
        assert await inbox.pending_count(agent) == 0

    async def test_nack_dead_letter_after_max_retries(self) -> None:
        inbox = InMemoryInbox()
        agent = _agent_id()
        msg = _message(target=agent)
        await inbox.deliver(agent, msg)
        for i in range(InMemoryInbox.MAX_RETRIES):
            await inbox.nack(agent, msg.id, error=f"err{i}")
        dead = await inbox.dead_letters(agent)
        assert len(dead) == 1
        assert dead[0].reason == DeadLetterReason.MAX_RETRIES
        assert dead[0].attempts == InMemoryInbox.MAX_RETRIES
        assert await inbox.pending_count(agent) == 0

    async def test_fifo_per_sender(self) -> None:
        inbox = InMemoryInbox()
        agent = _agent_id()
        sender = _agent_id("sender")
        msgs = [_message(sender=sender, target=agent) for _ in range(3)]
        for m in msgs:
            await inbox.deliver(agent, m)
        drained = await inbox.drain(agent, max=10)
        assert [m.id for m in drained] == [m.id for m in msgs]


# ---------------------------------------------------------------------------
# wakeup.py
# ---------------------------------------------------------------------------


class TestWakeup:
    def test_round_trip_json(self) -> None:
        w = Wakeup(kind="timer", at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        restored = Wakeup.model_validate_json(w.model_dump_json())
        assert restored == w

    def test_child_done_carries_ref(self) -> None:
        w = Wakeup(kind="child_done", child_run=new_run_id(), result_ref="ref:abc")
        assert w.child_run is not None
        assert w.result_ref == "ref:abc"

    def test_signal_carries_payload(self) -> None:
        w = Wakeup(kind="signal", signal="new_item", payload={"count": 3})
        assert w.signal == "new_item"
        assert w.payload == {"count": 3}


# ---------------------------------------------------------------------------
# scheduler.py
# ---------------------------------------------------------------------------


class TestRunRetryPolicy:
    def test_defaults(self) -> None:
        p = RunRetryPolicy()
        assert p.max_retries == 3
        assert p.backoff_s == 5.0
        assert p.dead_run_on_cancel is False

    def test_frozen(self) -> None:
        p = RunRetryPolicy()
        with pytest.raises((TypeError, Exception)):
            p.max_retries = 99  # type: ignore[misc]


class TestLease:
    def test_round_trip_json(self) -> None:
        from ravi.kernel.core.identity import AgentId

        lease = Lease(
            run_id=new_run_id(),
            agent_id=AgentId(type="agent", key="test"),
            worker_id="worker-1",
            expires_at=datetime(2026, 12, 31, tzinfo=timezone.utc),
        )
        restored = Lease.model_validate_json(lease.model_dump_json())
        assert restored == lease


# ---------------------------------------------------------------------------
# supervisor.py
# ---------------------------------------------------------------------------


class TestRunHandle:
    def test_frozen(self) -> None:
        h = RunHandle(
            run_id=new_run_id(), agent_id=_agent_id(), parent_run=new_run_id()
        )
        with pytest.raises((TypeError, Exception)):
            h.run_id = "x"  # type: ignore[misc]

    def test_round_trip_json(self) -> None:
        h = RunHandle(
            run_id=new_run_id(), agent_id=_agent_id(), parent_run=new_run_id()
        )
        restored = RunHandle.model_validate_json(h.model_dump_json())
        assert restored.run_id == h.run_id


class TestRunResult:
    def test_terminal_statuses(self) -> None:
        for status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED):
            r = RunResult(run_id=new_run_id(), status=status)
            assert r.status == status

    def test_round_trip_json(self) -> None:
        r = RunResult(
            run_id=new_run_id(),
            status=RunStatus.COMPLETED,
            error=None,
            metadata={"duration_ms": 42},
        )
        restored = RunResult.model_validate_json(r.model_dump_json())
        assert restored.status == RunStatus.COMPLETED
        assert restored.metadata["duration_ms"] == 42


# ---------------------------------------------------------------------------
# agent.py (Agent conformance)
# ---------------------------------------------------------------------------


class TestAgent:
    def test_minimal_impl_satisfies_protocol(self) -> None:
        class MinimalCtx:
            run_id: str = new_run_id()
            tenant_id: str | None = None

            def check(self) -> None: ...

        class MinimalAgent:
            def __init__(self) -> None:
                self.id = _agent_id()

            async def run(
                self, ctx: AgentRunContext, inbox: list[Message]
            ) -> None:
                pass

        agent = MinimalAgent()
        assert isinstance(agent, Agent)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# errors.py — new error types
# ---------------------------------------------------------------------------


class TestConcurrentAppendError:
    def test_fields(self) -> None:
        err = ConcurrentAppendError("race", run_id="r1", expected_seq=2, actual_seq=5)
        assert err.run_id == "r1"
        assert err.expected_seq == 2
        assert err.actual_seq == 5
        assert "race" in str(err)


class TestSpawnDenied:
    def test_fields(self) -> None:
        err = SpawnDenied("budget hit", parent_run="p1", budget=50)
        assert err.parent_run == "p1"
        assert err.budget == 50


# ---------------------------------------------------------------------------
# runtime_context.py — run_id extension
# ---------------------------------------------------------------------------


class TestRunMeta:
    def test_standalone_generates_run_id(self) -> None:
        meta = RunMeta.standalone()
        assert isinstance(meta.run_id, str)
        assert len(meta.run_id) > 0

    def test_standalone_accepts_explicit_run_id(self) -> None:
        rid = new_run_id()
        meta = RunMeta.standalone(run_id=rid)
        assert meta.run_id == rid

    def test_two_standalone_have_different_run_ids(self) -> None:
        m1 = RunMeta.standalone()
        m2 = RunMeta.standalone()
        assert m1.run_id != m2.run_id

    def test_run_id_from_supervision(self) -> None:
        agent = _agent_id()
        sup = Supervision.root(agent)
        token = CancellationToken()
        meta = RunMeta(cancellation=token, run_id=sup.run_id, supervision=sup)
        assert meta.run_id == sup.run_id
