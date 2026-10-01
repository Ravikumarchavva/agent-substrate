"""Conformance suite for ``RuntimeStore``.

Every implementation of the port — the SQLite default, the Postgres adapter, any
a consumer writes — must pass exactly these tests. They assert only what the port
promises, so they say nothing about how a store is built.

Use it by subclassing and providing the ``store`` fixture. The store must be
built with a clock fixed at ``NOW``: tests move time forward only through the
``now`` they pass to ``lease`` and ``heartbeat``, so that what the store stamps
itself (retry backoff, timestamps) is comparable with it::

    class TestMyStore(RuntimeStoreConformance):
        @pytest.fixture
        async def store(self): ...

Each test is one guarantee from ``abstractions/runtime/store.py``. When a guarantee
is violated the test name says which.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import pytest

from substrate.kernel.abstractions.agent.supervision import ExecutionBudget, Priority, SpawnBudget, Supervision
from substrate.kernel.abstractions.core.error_info import ErrorInfo
from substrate.kernel.abstractions.core.identity import Actor, Topic
from substrate.kernel.abstractions.exceptions import BudgetExhaustedError, LeaseLostError, ThreadBusyError
from substrate.kernel.abstractions.messaging.message import DataPayload, Message
from substrate.kernel.abstractions.runtime.ids import RunStatus
from substrate.kernel.abstractions.runtime.store import (
    Cancel,
    Commit,
    Complete,
    Delivery,
    Fail,
    HeartbeatResult,
    Nack,
    NewEntry,
    Retry,
    RunSpec,
    RuntimeStore,
    SignalSpec,
    SpawnSpec,
    Suspend,
)
from substrate.kernel.abstractions.runtime.wakeup import Wakeup

AGENT = Actor("agent", "a")
OTHER = Actor("agent", "b")
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


def msg(target: Actor = AGENT, sender: str = "s", **data: object) -> Message:
    return Message(target=target, sender=Actor.system(sender), payload=DataPayload(data=dict(data)))


class RuntimeStoreConformance:
    """Subclass and provide ``store``; every ``test_*`` here then runs against it."""

    @pytest.fixture
    async def store(self) -> RuntimeStore:  # pragma: no cover - supplied by subclasses
        raise NotImplementedError

    # -- helpers -------------------------------------------------------------

    @staticmethod
    async def lease_one(store: RuntimeStore, *, worker: str = "w1", at: datetime = NOW, lease_s: float = 30):
        leases = await store.lease(worker_id=worker, capacity=1, lease_s=lease_s, now=at)
        assert len(leases) == 1, "expected exactly one claimable run"
        return leases[0]

    # ======================================================================
    # runs & leasing
    # ======================================================================

    async def test_a_created_run_is_pending_and_leasable(self, store: RuntimeStore) -> None:
        run = await store.create_run(RunSpec(agent=AGENT))
        assert run.status == RunStatus.PENDING
        lease = await self.lease_one(store)
        assert lease.run_id == run.run_id and lease.epoch == 1
        assert (await store.get_run(run.run_id)).status == RunStatus.RUNNING

    async def test_a_leased_run_is_not_leased_again(self, store: RuntimeStore) -> None:
        await store.create_run(RunSpec(agent=AGENT))
        await self.lease_one(store, worker="w1")
        assert await store.lease(worker_id="w2", capacity=5, lease_s=30, now=NOW) == []

    async def test_leasing_respects_priority(self, store: RuntimeStore) -> None:
        low = await store.create_run(RunSpec(agent=Actor("agent", "low"), priority=Priority.LOW))
        high = await store.create_run(RunSpec(agent=Actor("agent", "high"), priority=Priority.HIGH))
        first = await self.lease_one(store)
        assert first.run_id == high.run_id
        assert (await self.lease_one(store, worker="w2")).run_id == low.run_id

    async def test_leasing_is_fair_across_tenants(self, store: RuntimeStore) -> None:
        for i in range(3):
            await store.create_run(RunSpec(agent=Actor("agent", f"a{i}"), tenant="noisy"))
        quiet = await store.create_run(RunSpec(agent=Actor("agent", "q"), tenant="quiet"))
        got = await store.lease(worker_id="w", capacity=2, lease_s=30, now=NOW)
        assert quiet.run_id in {lease.run_id for lease in got}, "a busy tenant starved a quiet one"

    async def test_a_second_active_run_in_a_thread_is_refused(self, store: RuntimeStore) -> None:
        await store.create_run(RunSpec(agent=AGENT, thread_id="t1"))
        with pytest.raises(ThreadBusyError):
            await store.create_run(RunSpec(agent=OTHER, thread_id="t1"))

    async def test_a_refused_create_delivers_nothing(self, store: RuntimeStore) -> None:
        await store.create_run(RunSpec(agent=AGENT, thread_id="t1"))
        with pytest.raises(ThreadBusyError):
            await store.create_run(RunSpec(agent=OTHER, thread_id="t1"), deliveries=[Delivery(agent=OTHER, msg=msg(OTHER))])
        assert await store.pending_count(OTHER) == 0

    async def test_a_thread_is_free_again_once_its_run_ends(self, store: RuntimeStore) -> None:
        await store.create_run(RunSpec(agent=AGENT, thread_id="t1"))
        lease = await self.lease_one(store)
        await store.commit(lease, Commit(outcome=Complete()))
        await store.create_run(RunSpec(agent=AGENT, thread_id="t1"))

    async def test_an_expired_lease_is_reclaimed_with_a_new_epoch(self, store: RuntimeStore) -> None:
        await store.create_run(RunSpec(agent=AGENT))
        first = await self.lease_one(store, worker="w1", lease_s=10)
        later = NOW + timedelta(seconds=11)
        second = (await store.lease(worker_id="w2", capacity=1, lease_s=10, now=later))[0]
        assert second.run_id == first.run_id and second.epoch == first.epoch + 1

    async def test_a_stale_lease_cannot_commit(self, store: RuntimeStore) -> None:
        await store.create_run(RunSpec(agent=AGENT))
        old = await self.lease_one(store, worker="w1", lease_s=10)
        await store.lease(worker_id="w2", capacity=1, lease_s=10, now=NOW + timedelta(seconds=11))
        with pytest.raises(LeaseLostError):
            await store.commit(old, Commit(entries=(NewEntry(kind="x"),), outcome=Complete()))
        assert await store.read_events(old.run_id) == [], "a fenced commit left something behind"

    async def test_a_stale_lease_cannot_append_live_output(self, store: RuntimeStore) -> None:
        await store.create_run(RunSpec(agent=AGENT))
        old = await self.lease_one(store, lease_s=10)
        await store.lease(worker_id="w2", capacity=1, lease_s=10, now=NOW + timedelta(seconds=11))
        with pytest.raises(LeaseLostError):
            await store.append_ephemeral(old, [NewEntry(kind="text.delta", payload={"text": "x"})])

    async def test_heartbeat_reports_ok_cancel_deadline_and_lost(self, store: RuntimeStore) -> None:
        run = await store.create_run(RunSpec(agent=AGENT, deadline=NOW + timedelta(seconds=100)))
        lease = await self.lease_one(store, lease_s=10)
        assert await store.heartbeat(lease, lease_s=10, now=NOW + timedelta(seconds=1)) == HeartbeatResult.OK
        assert await store.heartbeat(lease, lease_s=10, now=NOW + timedelta(seconds=101)) == HeartbeatResult.DEADLINE
        await store.request_cancel(run.run_id, reason="stop")
        assert await store.heartbeat(lease, lease_s=10, now=NOW + timedelta(seconds=2)) == HeartbeatResult.CANCEL_REQUESTED
        await store.commit(lease, Commit(outcome=Cancel()))
        assert await store.heartbeat(lease, lease_s=10, now=NOW + timedelta(seconds=3)) == HeartbeatResult.LOST

    async def test_a_healthy_heartbeat_keeps_the_lease_alive(self, store: RuntimeStore) -> None:
        """The failure this guards: a heartbeat that reports 'stop' to a healthy run
        cancelled every run after fifteen seconds."""
        await store.create_run(RunSpec(agent=AGENT))
        lease = await self.lease_one(store, lease_s=10)
        for second in (5, 10, 15, 20):
            assert await store.heartbeat(lease, lease_s=10, now=NOW + timedelta(seconds=second)) == HeartbeatResult.OK
        assert await store.lease(worker_id="w2", capacity=1, lease_s=10, now=NOW + timedelta(seconds=25)) == []

    async def test_a_run_past_its_deadline_ends_even_if_nobody_runs_it(self, store: RuntimeStore) -> None:
        run = await store.create_run(RunSpec(agent=AGENT, deadline=NOW + timedelta(seconds=5)))
        assert await store.lease(worker_id="w", capacity=1, lease_s=10, now=NOW + timedelta(seconds=6)) == []
        record = await store.get_run(run.run_id)
        assert record.status == RunStatus.FAILED

    # ======================================================================
    # journal
    # ======================================================================

    async def test_entries_are_appended_in_order_with_contiguous_seq(self, store: RuntimeStore) -> None:
        await store.create_run(RunSpec(agent=AGENT))
        lease = await self.lease_one(store)
        await store.commit(lease, Commit(entries=(NewEntry(kind="a"), NewEntry(kind="b"))))
        await store.commit(lease, Commit(entries=(NewEntry(kind="c"),)))
        events = await store.read_events(lease.run_id)
        assert [e.seq for e in events] == [0, 1, 2] and [e.kind for e in events] == ["a", "b", "c"]
        assert await store.last_seq(lease.run_id) == 2

    async def test_a_run_with_no_log_reports_minus_one(self, store: RuntimeStore) -> None:
        run = await store.create_run(RunSpec(agent=AGENT))
        assert await store.last_seq(run.run_id) == -1

    async def test_a_repeated_dedup_key_appends_once(self, store: RuntimeStore) -> None:
        await store.create_run(RunSpec(agent=AGENT))
        lease = await self.lease_one(store)
        entry = NewEntry(kind="user.message", dedup_key="um")
        first = await store.commit(lease, Commit(entries=(entry,)))
        second = await store.commit(lease, Commit(entries=(entry,)))
        assert first.seqs == second.seqs
        assert len(await store.read_events(lease.run_id)) == 1

    async def test_reading_from_a_seq_returns_the_suffix(self, store: RuntimeStore) -> None:
        await store.create_run(RunSpec(agent=AGENT))
        lease = await self.lease_one(store)
        await store.commit(lease, Commit(entries=tuple(NewEntry(kind=f"k{i}") for i in range(5))))
        assert [e.seq for e in await store.read_events(lease.run_id, from_seq=3)] == [3, 4]

    async def test_live_output_is_visible_to_a_tail_but_not_to_a_replay(self, store: RuntimeStore) -> None:
        await store.create_run(RunSpec(agent=AGENT))
        lease = await self.lease_one(store)
        await store.append_ephemeral(lease, [NewEntry(kind="text.delta", payload={"text": "hi"})])
        await store.commit(lease, Commit(entries=(NewEntry(kind="effect.result"),)))
        assert [e.kind for e in await store.read_events(lease.run_id)] == ["text.delta", "effect.result"]
        assert [e.kind for e in await store.read_events(lease.run_id, durable_only=True)] == ["effect.result"]

    async def test_the_journal_does_not_grow_with_streamed_tokens(self, store: RuntimeStore) -> None:
        await store.create_run(RunSpec(agent=AGENT))
        lease = await self.lease_one(store)
        await store.append_ephemeral(lease, [NewEntry(kind="text.delta") for _ in range(200)])
        await store.commit(lease, Commit(entries=(NewEntry(kind="effect.result"),), outcome=Complete()))
        assert len(await store.read_events(lease.run_id, durable_only=True)) == 2  # the effect and the terminal

    # ======================================================================
    # terminal transition — one atomic unit
    # ======================================================================

    async def test_completing_writes_one_terminal_entry_and_sets_the_status(self, store: RuntimeStore) -> None:
        await store.create_run(RunSpec(agent=AGENT))
        lease = await self.lease_one(store)
        result = await store.commit(lease, Commit(outcome=Complete()))
        assert result.status == RunStatus.COMPLETED
        assert [e.kind for e in await store.read_events(lease.run_id)] == ["run.completed"]

    async def test_a_run_ends_exactly_once(self, store: RuntimeStore) -> None:
        await store.create_run(RunSpec(agent=AGENT))
        lease = await self.lease_one(store)
        await store.commit(lease, Commit(outcome=Complete()))
        with pytest.raises(LeaseLostError):
            await store.commit(lease, Commit(outcome=Fail(error=ErrorInfo(code="x", message="late"))))
        kinds = [e.kind for e in await store.read_events(lease.run_id)]
        assert kinds.count("run.completed") + kinds.count("run.failed") == 1

    async def test_the_terminal_commit_acks_the_inbox_in_the_same_transaction(self, store: RuntimeStore) -> None:
        m = msg()
        await store.create_run(RunSpec(agent=AGENT), deliveries=[Delivery(agent=AGENT, msg=m)])
        lease = await self.lease_one(store)
        await store.commit(lease, Commit(ack=(m.id,), outcome=Complete()))
        assert await store.pending_count(AGENT) == 0

    async def test_a_terminal_commit_that_is_refused_acks_nothing(self, store: RuntimeStore) -> None:
        m = msg()
        await store.create_run(RunSpec(agent=AGENT), deliveries=[Delivery(agent=AGENT, msg=m)])
        old = await self.lease_one(store, lease_s=10)
        await store.lease(worker_id="w2", capacity=1, lease_s=10, now=NOW + timedelta(seconds=11))
        with pytest.raises(LeaseLostError):
            await store.commit(old, Commit(ack=(m.id,), outcome=Complete()))
        assert await store.pending_count(AGENT) == 1, "a fenced commit acknowledged a message"

    async def test_failing_records_the_error_on_the_run(self, store: RuntimeStore) -> None:
        run = await store.create_run(RunSpec(agent=AGENT))
        lease = await self.lease_one(store)
        error = ErrorInfo(code="boom", message="it broke")
        await store.commit(lease, Commit(outcome=Fail(error=error)))
        record = await store.get_run(run.run_id)
        assert record.status == RunStatus.FAILED and "boom" in (record.result.error or "")

    async def test_a_retry_parks_the_run_until_its_backoff_passes(self, store: RuntimeStore) -> None:
        await store.create_run(RunSpec(agent=AGENT))
        lease = await self.lease_one(store)
        error = ErrorInfo(code="blip", message="try again", retryable=True)
        await store.commit(lease, Commit(outcome=Retry(error=error, delay_s=60)))
        assert await store.lease(worker_id="w", capacity=1, lease_s=10, now=NOW + timedelta(seconds=30)) == []
        again = (await store.lease(worker_id="w", capacity=1, lease_s=10, now=NOW + timedelta(seconds=61)))[0]
        assert again.run_id == lease.run_id and again.attempt == 2 and again.retry_count == 1

    # ======================================================================
    # inbox
    # ======================================================================

    async def test_a_delivered_message_is_drained_until_acknowledged(self, store: RuntimeStore) -> None:
        m = msg()
        await store.create_run(RunSpec(agent=AGENT), deliveries=[Delivery(agent=AGENT, msg=m)])
        assert [x.id for x in await store.drain(AGENT)] == [m.id]
        assert [x.id for x in await store.drain(AGENT)] == [m.id], "draining must not remove"

    async def test_redelivery_before_ack_is_a_noop(self, store: RuntimeStore) -> None:
        m = msg()
        assert (await store.deliver(Delivery(agent=AGENT, msg=m), wake=False)).accepted
        assert not (await store.deliver(Delivery(agent=AGENT, msg=m), wake=False)).accepted
        assert await store.pending_count(AGENT) == 1

    async def test_redelivery_after_ack_is_still_refused(self, store: RuntimeStore) -> None:
        """An at-least-once transport redelivers after the consumer committed. The
        inbox is what absorbs that; forgetting at ack hands the agent the message twice."""
        m = msg()
        await store.create_run(RunSpec(agent=AGENT), deliveries=[Delivery(agent=AGENT, msg=m)])
        lease = await self.lease_one(store)
        await store.commit(lease, Commit(ack=(m.id,)))
        assert not (await store.deliver(Delivery(agent=AGENT, msg=m), wake=False)).accepted
        assert await store.drain(AGENT) == []

    async def test_messages_from_one_sender_drain_in_arrival_order(self, store: RuntimeStore) -> None:
        sent = [msg(n=i) for i in range(5)]
        for m in sent:
            await store.deliver(Delivery(agent=AGENT, msg=m), wake=False)
        assert [x.id for x in await store.drain(AGENT)] == [m.id for m in sent]

    async def test_delivering_to_an_idle_agent_creates_a_run_for_it(self, store: RuntimeStore) -> None:
        result = await store.deliver(Delivery(agent=AGENT, msg=msg()))
        assert result.created_run is not None
        assert (await store.get_run(result.created_run)).status == RunStatus.PENDING

    async def test_delivering_to_a_suspended_agent_wakes_it(self, store: RuntimeStore) -> None:
        await store.create_run(RunSpec(agent=AGENT))
        lease = await self.lease_one(store)
        await store.commit(lease, Commit(outcome=Suspend(wake=Wakeup(kind="signal", signals=["never"]))))
        result = await store.deliver(Delivery(agent=AGENT, msg=msg()))
        assert result.woke_run == lease.run_id
        assert (await store.get_run(lease.run_id)).status == RunStatus.PENDING

    async def test_delivering_to_a_running_agent_does_not_create_a_second_run(self, store: RuntimeStore) -> None:
        await store.create_run(RunSpec(agent=AGENT))
        await self.lease_one(store)
        result = await store.deliver(Delivery(agent=AGENT, msg=msg()))
        assert result.created_run is None and result.woke_run is None

    async def test_a_message_that_keeps_failing_is_dead_lettered_and_stays_refused(self, store: RuntimeStore) -> None:
        m = msg()
        await store.create_run(RunSpec(agent=AGENT), deliveries=[Delivery(agent=AGENT, msg=m)])
        lease = await self.lease_one(store)
        error = ErrorInfo(code="boom", message="bad")
        for _ in range(3):
            await store.commit(lease, Commit(nack=(Nack(msg_id=m.id, error=error),)))
        assert [d.msg.id for d in await store.dead_letters(AGENT)] == [m.id]
        assert await store.pending_count(AGENT) == 0
        assert not (await store.deliver(Delivery(agent=AGENT, msg=m), wake=False)).accepted

    async def test_a_dead_letter_can_be_redriven(self, store: RuntimeStore) -> None:
        m = msg()
        await store.create_run(RunSpec(agent=AGENT), deliveries=[Delivery(agent=AGENT, msg=m)])
        lease = await self.lease_one(store)
        error = ErrorInfo(code="boom", message="bad")
        for _ in range(3):
            await store.commit(lease, Commit(nack=(Nack(msg_id=m.id, error=error),)))
        assert await store.redrive(AGENT, m.id) is True
        assert await store.dead_letters(AGENT) == []
        assert [x.id for x in await store.drain(AGENT)] == [m.id]
        assert await store.redrive(AGENT, "nothing") is False

    # ======================================================================
    # signals & suspension
    # ======================================================================

    async def test_a_signal_is_consumed_once_per_claim(self, store: RuntimeStore) -> None:
        run = await store.create_run(RunSpec(agent=AGENT))
        await store.signal(run.run_id, "go", {"v": 1})
        assert await store.consume(run.run_id, "go", "claim-1") == {"v": 1}
        assert await store.consume(run.run_id, "go", "claim-1") == {"v": 1}, "the same claim must return the same payload"
        assert await store.consume(run.run_id, "go", "claim-2") is None, "the signal was already claimed"

    async def test_signals_are_consumed_oldest_first(self, store: RuntimeStore) -> None:
        run = await store.create_run(RunSpec(agent=AGENT))
        for i in range(3):
            await store.signal(run.run_id, "n", {"i": i})
        assert [(await store.consume(run.run_id, "n", f"c{i}"))["i"] for i in range(3)] == [0, 1, 2]

    async def test_a_signal_wakes_a_run_waiting_on_it(self, store: RuntimeStore) -> None:
        await store.create_run(RunSpec(agent=AGENT))
        lease = await self.lease_one(store)
        await store.commit(lease, Commit(outcome=Suspend(wake=Wakeup(kind="signal", signals=["go"]))))
        assert (await store.get_run(lease.run_id)).status == RunStatus.SUSPENDED
        assert await store.signal(lease.run_id, "go", {}) is True
        assert (await store.get_run(lease.run_id)).status == RunStatus.PENDING

    async def test_a_signal_for_a_different_name_does_not_wake_it(self, store: RuntimeStore) -> None:
        await store.create_run(RunSpec(agent=AGENT))
        lease = await self.lease_one(store)
        await store.commit(lease, Commit(outcome=Suspend(wake=Wakeup(kind="signal", signals=["go"]))))
        assert await store.signal(lease.run_id, "other", {}) is False
        assert (await store.get_run(lease.run_id)).status == RunStatus.SUSPENDED

    async def test_a_signal_that_arrived_before_suspending_prevents_the_sleep(self, store: RuntimeStore) -> None:
        """The lost-wakeup race. The signal lands after the run looked and before it
        slept; if suspension does not check the buffer, the run sleeps forever with
        its answer sitting unread."""
        await store.create_run(RunSpec(agent=AGENT))
        lease = await self.lease_one(store)
        await store.signal(lease.run_id, "go", {"v": 1})
        result = await store.commit(lease, Commit(outcome=Suspend(wake=Wakeup(kind="signal", signals=["go"]))))
        assert result.resumed_immediately
        assert (await store.get_run(lease.run_id)).status == RunStatus.PENDING

    async def test_a_timer_wakes_a_suspended_run_when_due(self, store: RuntimeStore) -> None:
        await store.create_run(RunSpec(agent=AGENT))
        lease = await self.lease_one(store)
        at = NOW + timedelta(seconds=60)
        await store.commit(lease, Commit(outcome=Suspend(wake=Wakeup(kind="timer", at=at))))
        assert await store.lease(worker_id="w", capacity=1, lease_s=10, now=NOW + timedelta(seconds=30)) == []
        assert len(await store.lease(worker_id="w", capacity=1, lease_s=10, now=at + timedelta(seconds=1))) == 1

    async def test_a_signal_commit_can_wake_another_run_atomically(self, store: RuntimeStore) -> None:
        waiting = await store.create_run(RunSpec(agent=OTHER))
        waiter = await self.lease_one(store, worker="w1")
        await store.commit(waiter, Commit(outcome=Suspend(wake=Wakeup(kind="signal", signals=["ping"]))))
        await store.create_run(RunSpec(agent=AGENT))
        sender = await self.lease_one(store, worker="w2")
        await store.commit(sender, Commit(signals=(SignalSpec(run_id=waiting.run_id, name="ping"),), outcome=Complete()))
        assert (await store.get_run(waiting.run_id)).status == RunStatus.PENDING

    async def test_find_a_run_by_the_signal_it_waits_on(self, store: RuntimeStore) -> None:
        await store.create_run(RunSpec(agent=AGENT))
        lease = await self.lease_one(store)
        await store.commit(lease, Commit(outcome=Suspend(wake=Wakeup(kind="signal", signals=["hitl:1", "hitl:2"]))))
        found = await store.find_runs(wake_signal="hitl:2")
        assert [r.run_id for r in found] == [lease.run_id]
        assert await store.find_runs(wake_signal="hitl:9") == []

    # ======================================================================
    # supervision
    # ======================================================================

    def _child_spec(self, parent_supervision: Supervision | None = None, agent: Actor = OTHER) -> RunSpec:
        return RunSpec(agent=agent, supervision=parent_supervision)

    async def test_a_spawn_is_idempotent_by_effect_id(self, store: RuntimeStore) -> None:
        await store.create_run(RunSpec(agent=AGENT))
        lease = await self.lease_one(store)
        spawn = SpawnSpec(effect_id="e1", child=RunSpec(agent=OTHER), boot=msg(OTHER))
        first = await store.commit(lease, Commit(spawns=(spawn,)))
        second = await store.commit(lease, Commit(spawns=(spawn,)))
        assert first.spawned["e1"].run_id == second.spawned["e1"].run_id
        assert len(await store.children(lease.run_id)) == 1

    async def test_a_spawn_delivers_the_boot_message_to_the_child(self, store: RuntimeStore) -> None:
        await store.create_run(RunSpec(agent=AGENT))
        lease = await self.lease_one(store)
        boot = msg(OTHER, hello=1)
        await store.commit(lease, Commit(spawns=(SpawnSpec(effect_id="e", child=RunSpec(agent=OTHER), boot=boot),)))
        assert [m.id for m in await store.drain(OTHER)] == [boot.id]

    async def test_the_spawn_is_journaled_with_the_commit(self, store: RuntimeStore) -> None:
        await store.create_run(RunSpec(agent=AGENT))
        lease = await self.lease_one(store)
        await store.commit(lease, Commit(spawns=(SpawnSpec(effect_id="e", child=RunSpec(agent=OTHER), boot=msg(OTHER)),)))
        assert "child.spawned" in [e.kind for e in await store.read_events(lease.run_id)]

    async def test_a_child_ending_wakes_a_parent_waiting_on_it(self, store: RuntimeStore) -> None:
        await store.create_run(RunSpec(agent=AGENT))
        parent = await self.lease_one(store, worker="p")
        result = await store.commit(parent, Commit(spawns=(SpawnSpec(effect_id="e", child=RunSpec(agent=OTHER), boot=msg(OTHER)),)))
        child_id = result.spawned["e"].run_id
        await store.commit(parent, Commit(outcome=Suspend(wake=Wakeup(kind="signal", signals=[f"child:{child_id}"]))))
        child = await self.lease_one(store, worker="c")
        await store.commit(child, Commit(outcome=Complete()))
        assert (await store.get_run(parent.run_id)).status == RunStatus.PENDING
        payload = await store.consume(parent.run_id, f"child:{child_id}", "claim")
        assert payload["status"] == "completed"

    async def test_a_child_that_fails_tells_its_parent_how(self, store: RuntimeStore) -> None:
        await store.create_run(RunSpec(agent=AGENT))
        parent = await self.lease_one(store, worker="p")
        result = await store.commit(parent, Commit(spawns=(SpawnSpec(effect_id="e", child=RunSpec(agent=OTHER), boot=msg(OTHER)),)))
        child_id = result.spawned["e"].run_id
        child = await self.lease_one(store, worker="c")
        await store.commit(child, Commit(outcome=Fail(error=ErrorInfo(code="x", message="y"))))
        payload = await store.consume(parent.run_id, f"child:{child_id}", "claim")
        assert payload["kind"] == "target_failed"

    async def test_a_spawn_beyond_the_headcount_cap_is_refused_whole(self, store: RuntimeStore) -> None:
        supervision = Supervision.root(AGENT, spawn_budget=SpawnBudget(max_agents=2))
        await store.create_run(RunSpec(agent=AGENT, supervision=supervision))
        lease = await self.lease_one(store)
        await store.commit(lease, Commit(spawns=(SpawnSpec(effect_id="a", child=RunSpec(agent=Actor("agent", "c1"), supervision=supervision), boot=msg()),)))
        with pytest.raises(BudgetExhaustedError):
            await store.commit(
                lease,
                Commit(
                    entries=(NewEntry(kind="should.not.land"),),
                    spawns=(SpawnSpec(effect_id="b", child=RunSpec(agent=Actor("agent", "c2"), supervision=supervision), boot=msg()),),
                ),
            )
        assert "should.not.land" not in [e.kind for e in await store.read_events(lease.run_id)]

    async def test_cancelling_a_pending_run_ends_it_on_the_spot(self, store: RuntimeStore) -> None:
        run = await store.create_run(RunSpec(agent=AGENT))
        assert await store.request_cancel(run.run_id, reason="no") == [run.run_id]
        assert (await store.get_run(run.run_id)).status == RunStatus.CANCELLED
        assert [e.kind for e in await store.read_events(run.run_id)] == ["run.cancelled"]

    async def test_cancel_cascades_to_every_descendant(self, store: RuntimeStore) -> None:
        await store.create_run(RunSpec(agent=AGENT))
        parent = await self.lease_one(store, worker="p")
        result = await store.commit(parent, Commit(spawns=(SpawnSpec(effect_id="e", child=RunSpec(agent=OTHER), boot=msg(OTHER)),)))
        child_id = result.spawned["e"].run_id
        await store.commit(parent, Commit(outcome=Suspend(wake=Wakeup(kind="signal", signals=["x"]))))
        affected = await store.request_cancel(parent.run_id, reason="stop")
        assert set(affected) == {parent.run_id, child_id}
        assert (await store.get_run(child_id)).status == RunStatus.CANCELLED

    async def test_cancelling_a_running_run_is_observed_at_its_next_heartbeat(self, store: RuntimeStore) -> None:
        run = await store.create_run(RunSpec(agent=AGENT))
        lease = await self.lease_one(store)
        await store.request_cancel(run.run_id, reason="stop")
        assert (await store.get_run(run.run_id)).status == RunStatus.RUNNING
        assert await store.heartbeat(lease, lease_s=10, now=NOW) == HeartbeatResult.CANCEL_REQUESTED

    async def test_a_failed_parent_does_not_leave_its_children_running(self, store: RuntimeStore) -> None:
        await store.create_run(RunSpec(agent=AGENT))
        parent = await self.lease_one(store, worker="p")
        result = await store.commit(parent, Commit(spawns=(SpawnSpec(effect_id="e", child=RunSpec(agent=OTHER), boot=msg(OTHER)),)))
        await store.commit(parent, Commit(outcome=Fail(error=ErrorInfo(code="x", message="y"))))
        assert (await store.get_run(result.spawned["e"].run_id)).status == RunStatus.CANCELLED

    # ======================================================================
    # follow graph
    # ======================================================================

    async def test_following_is_idempotent_and_reversible(self, store: RuntimeStore) -> None:
        topic = Topic("news")
        await store.follow(AGENT, topic)
        await store.follow(AGENT, topic)
        await store.follow(OTHER, topic)
        assert await store.followers_of(topic) == sorted([AGENT, OTHER], key=str)
        await store.unfollow(AGENT, topic)
        await store.unfollow(AGENT, topic)
        assert await store.followers_of(topic) == [OTHER]

    # ======================================================================
    # operations
    # ======================================================================

    async def test_erasing_a_tenant_removes_every_trace_of_it(self, store: RuntimeStore) -> None:
        secret = msg(AGENT, text="alice's private message")
        await store.create_run(RunSpec(agent=AGENT, tenant="acme"), deliveries=[Delivery(agent=AGENT, msg=secret, tenant="acme")])
        lease = await self.lease_one(store)
        await store.commit(
            lease,
            Commit(
                entries=(NewEntry(kind="user.message", payload={"text": "alice's private message"}),),
                ack=(secret.id,),
                outcome=Complete(),
            ),
        )
        await store.create_run(RunSpec(agent=OTHER, tenant="globex"))
        assert await store.erase(tenant="acme") == 1
        assert await store.read_events(lease.run_id) == []
        assert await store.get_run(lease.run_id) is None
        assert await store.pending_count(AGENT) == 0
        assert len(await store.find_runs(tenant="globex")) == 1, "erasing one tenant touched another"

    async def test_mail_left_unread_when_a_run_ends_gets_a_new_run(self, store: RuntimeStore) -> None:
        """A message that arrived while the run was finishing must not be stranded
        with nobody to read it."""
        await store.create_run(RunSpec(agent=AGENT))
        lease = await self.lease_one(store)
        await store.deliver(Delivery(agent=AGENT, msg=msg(late=1)), wake=False)
        await store.commit(lease, Commit(outcome=Complete()))
        follow_up = await store.find_runs(agent=AGENT)
        assert len(follow_up) == 1 and follow_up[0].run_id != lease.run_id

    async def test_stats_count_runs_by_state(self, store: RuntimeStore) -> None:
        await store.create_run(RunSpec(agent=Actor("agent", "1")))
        await store.create_run(RunSpec(agent=Actor("agent", "2")))
        await self.lease_one(store)
        stats = await store.stats()
        assert stats.pending == 1 and stats.running == 1

    async def test_pruning_forgets_old_acks_and_terminal_runs(self, store: RuntimeStore) -> None:
        m = msg()
        await store.create_run(RunSpec(agent=AGENT), deliveries=[Delivery(agent=AGENT, msg=m)])
        lease = await self.lease_one(store)
        await store.commit(lease, Commit(ack=(m.id,), outcome=Complete()))
        assert await store.prune(before=NOW + timedelta(days=1)) >= 1
        assert await store.get_run(lease.run_id) is None


__all__ = ["RuntimeStoreConformance", "AGENT", "OTHER", "NOW", "msg"]
