"""Section 3 regression tests — lease registry, backpressure, lifecycle.

Each test pins one new contract or behaviour so the runtime stays correct
under hyperscale + Python 3.14 free-threaded conditions.

Lease registry
~~~~~~~~~~~~~~
- Single-writer invariant under sequential and threaded contention
- Renewal preserves identity, fails on stolen leases
- TTL expiry releases the slot for the next acquirer

Backpressure
~~~~~~~~~~~~
- ``SHED`` raises ``MailboxFullError`` and emits ``BackpressureSignal``
- ``DROP_NEWEST`` discards the incoming envelope silently
- ``DROP_OLDEST`` evicts the head and accepts the new envelope
- Dispatcher fans signals to every registered observer

LocalRuntime
~~~~~~~~~~~~
- ``_ensure_agent`` walks DORMANT → ACTIVATING → ACTIVE under the
  configured ``LeaseRegistry``; ``hibernate`` walks ACTIVE → DORMANT
- A second runtime sharing the registry fails ``LeaseAcquisitionFailed``
- ``stop`` releases every held lease

Partition affinity
~~~~~~~~~~~~~~~~~~
- Publishes carrying ``locality.partition_key`` route only to the
  matching agent instance (preserves per-partition ordering).
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from ravi.kernel.contracts._coordination import LocalityHint
from ravi.kernel.messages.content import TextBlock
from ravi.kernel.runtime import (
    AgentId,
    AgentLifecycleState,
    BackpressureAction,
    BackpressurePolicy,
    BackpressureSignal,
    Envelope,
    InMemoryLeaseRegistry,
    LeaseAcquisitionFailed,
    MailboxFullError,
    MessageContext,
    TopicId
)
from ravi.fabric.runtime.mailbox import Mailbox
from ravi.fabric.runtime.local import LocalRuntime


# ---------------------------------------------------------------------------
# Lease registry — single-writer invariant
# ---------------------------------------------------------------------------


class TestLeaseRegistry:
    async def test_acquire_grants_lease_to_first_caller(self) -> None:
        registry = InMemoryLeaseRegistry()
        aid = AgentId("a", "1")

        first = await registry.acquire(aid, "worker-1", ttl_seconds=5.0)
        assert first.acquired
        assert first.lease is not None
        assert first.lease.worker_id == "worker-1"

    async def test_acquire_refuses_second_caller(self) -> None:
        registry = InMemoryLeaseRegistry()
        aid = AgentId("a", "1")

        first = await registry.acquire(aid, "worker-1", ttl_seconds=5.0)
        second = await registry.acquire(aid, "worker-2", ttl_seconds=5.0)

        assert first.acquired
        assert not second.acquired
        assert second.lease is None
        assert second.current_holder is not None
        assert second.current_holder.worker_id == "worker-1"
        assert second.current_holder.lease_id == first.lease.lease_id

    async def test_acquire_after_release_succeeds(self) -> None:
        registry = InMemoryLeaseRegistry()
        aid = AgentId("a", "1")

        first = await registry.acquire(aid, "worker-1", ttl_seconds=5.0)
        await registry.release(first.lease)
        second = await registry.acquire(aid, "worker-2", ttl_seconds=5.0)

        assert second.acquired
        assert second.lease.worker_id == "worker-2"

    async def test_acquire_after_ttl_expiry_succeeds(self) -> None:
        registry = InMemoryLeaseRegistry()
        aid = AgentId("a", "1")

        first = await registry.acquire(aid, "worker-1", ttl_seconds=0.05)
        assert first.acquired
        await asyncio.sleep(0.1)
        second = await registry.acquire(aid, "worker-2", ttl_seconds=5.0)
        assert second.acquired
        assert second.lease.worker_id == "worker-2"

    async def test_renew_preserves_lease_id(self) -> None:
        registry = InMemoryLeaseRegistry()
        aid = AgentId("a", "1")
        result = await registry.acquire(aid, "worker-1", ttl_seconds=1.0)
        renewed = await registry.renew(result.lease, ttl_seconds=5.0)
        assert renewed is not None
        assert renewed.lease_id == result.lease.lease_id
        assert renewed.expires_at != result.lease.expires_at

    async def test_renew_lost_lease_returns_none(self) -> None:
        registry = InMemoryLeaseRegistry()
        aid = AgentId("a", "1")
        first = await registry.acquire(aid, "worker-1", ttl_seconds=0.05)
        await asyncio.sleep(0.1)
        # Steal it
        second = await registry.acquire(aid, "worker-2", ttl_seconds=5.0)
        assert second.acquired
        # Try to renew the stolen lease
        renewed = await registry.renew(first.lease, ttl_seconds=5.0)
        assert renewed is None

    async def test_release_wrong_lease_is_noop(self) -> None:
        registry = InMemoryLeaseRegistry()
        aid = AgentId("a", "1")
        first = await registry.acquire(aid, "worker-1", ttl_seconds=5.0)
        # Forge a different lease_id
        from dataclasses import replace

        bogus = replace(first.lease, lease_id="bogus")
        await registry.release(bogus)
        # Original lease still active
        current = await registry.current(aid)
        assert current is not None
        assert current.lease_id == first.lease.lease_id

    async def test_current_returns_none_after_expiry(self) -> None:
        registry = InMemoryLeaseRegistry()
        aid = AgentId("a", "1")
        await registry.acquire(aid, "worker-1", ttl_seconds=0.05)
        await asyncio.sleep(0.1)
        assert await registry.current(aid) is None

    async def test_ttl_zero_rejected(self) -> None:
        registry = InMemoryLeaseRegistry()
        aid = AgentId("a", "1")
        with pytest.raises(ValueError):
            await registry.acquire(aid, "w", ttl_seconds=0.0)

    async def test_threaded_acquire_single_winner(self) -> None:
        """Many threads race for the same agent — exactly one wins."""
        registry = InMemoryLeaseRegistry()
        aid = AgentId("a", "1")
        winners: list[str] = []
        winners_lock = threading.Lock()
        barrier = threading.Barrier(8)

        def race(worker_id: str) -> None:
            barrier.wait()
            result = asyncio.run(
                registry.acquire(aid, worker_id, ttl_seconds=30.0)
            )
            if result.acquired:
                with winners_lock:
                    winners.append(worker_id)

        threads = [
            threading.Thread(target=race, args=(f"w-{i}",)) for i in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(winners) == 1, f"expected exactly 1 winner, got {winners}"


# ---------------------------------------------------------------------------
# Backpressure
# ---------------------------------------------------------------------------


def _make_envelope(payload: str = "x") -> Envelope:
    return Envelope(
        sender=None,
        target=AgentId("worker", "1"),
        content=[TextBlock(text=payload)],
    )


class TestBackpressureMailbox:
    def test_shed_policy_raises_and_returns_signal(self) -> None:
        mb = Mailbox(capacity=1, policy=BackpressurePolicy.SHED)
        env1 = _make_envelope("first")
        env2 = _make_envelope("second")

        action = mb.put_nowait(env1)
        assert action is BackpressureAction.ACCEPTED

        with pytest.raises(MailboxFullError):
            mb.put_nowait(env2)

    def test_drop_newest_silently_discards(self) -> None:
        mb = Mailbox(capacity=1, policy=BackpressurePolicy.DROP_NEWEST)
        env1 = _make_envelope("first")
        env2 = _make_envelope("second")

        assert mb.put_nowait(env1) is BackpressureAction.ACCEPTED
        action = mb.put_nowait(env2)
        assert action is BackpressureAction.DROPPED_NEWEST
        assert mb.size == 1

    def test_drop_oldest_evicts_and_accepts(self) -> None:
        mb = Mailbox(capacity=1, policy=BackpressurePolicy.DROP_OLDEST)
        env1 = _make_envelope("first")
        env2 = _make_envelope("second")

        assert mb.put_nowait(env1) is BackpressureAction.ACCEPTED
        action = mb.put_nowait(env2)
        assert action is BackpressureAction.DROPPED_OLDEST
        assert mb.size == 1

    def test_block_policy_falls_through_to_shed_on_nowait(self) -> None:
        mb = Mailbox(capacity=1, policy=BackpressurePolicy.BLOCK)
        env1 = _make_envelope("first")
        env2 = _make_envelope("second")

        assert mb.put_nowait(env1) is BackpressureAction.ACCEPTED
        # BLOCK is not applicable to put_nowait — fail loud
        with pytest.raises(MailboxFullError):
            mb.put_nowait(env2)


class TestBackpressureDispatcherSignals:
    async def test_shed_emits_signal_to_observer(self) -> None:
        rt = LocalRuntime(mailbox_capacity=1)
        await rt.start()
        signals: list[BackpressureSignal] = []
        rt.dispatcher.add_backpressure_observer(signals.append)

        # Slow handler to keep the mailbox full
        gate = asyncio.Event()

        async def slow_handler(ctx: MessageContext, payload: Any) -> str:
            await gate.wait()
            return "done"

        topic = TopicId("events", "s1")
        await rt.register("slow", slow_handler)
        await rt.subscribe("slow", topic)

        # Ensure the agent exists
        await rt.publish_message("warmup", topic=topic)
        await asyncio.sleep(0.01)
        # Flood the topic — must shed at least once
        for i in range(5):
            await rt.publish_message(f"flood-{i}", topic=topic)

        # Let the slow handler clear so we can stop
        gate.set()
        await rt.stop_when_idle(poll_interval=0.01)

        assert len(signals) >= 1, "shed should emit at least one BackpressureSignal"
        sig = signals[0]
        assert sig.action is BackpressureAction.SHED
        assert sig.policy is BackpressurePolicy.SHED
        assert sig.capacity == 1

    def test_multiple_observers_all_called(self) -> None:
        rt = LocalRuntime(mailbox_capacity=1)
        a: list[BackpressureSignal] = []
        b: list[BackpressureSignal] = []
        rt.dispatcher.add_backpressure_observer(a.append)
        rt.dispatcher.add_backpressure_observer(b.append)
        # Synthesize a signal directly
        signal = BackpressureSignal(
            target=AgentId("x", "1"),
            policy=BackpressurePolicy.SHED,
            action=BackpressureAction.SHED,
            queue_depth=1,
            capacity=1,
            correlation_id="abc",
        )
        rt.dispatcher._emit_backpressure(signal)
        assert a == [signal]
        assert b == [signal]


# ---------------------------------------------------------------------------
# LocalRuntime — lease + lifecycle wiring
# ---------------------------------------------------------------------------


async def _noop_handler(ctx: MessageContext, payload: Any) -> str:
    return "ok"


class TestLocalRuntimeLeaseIntegration:
    async def test_ensure_agent_acquires_lease(self) -> None:
        registry = InMemoryLeaseRegistry()
        rt = LocalRuntime(lease_registry=registry, worker_id="worker-A")
        await rt.register("worker", _noop_handler)
        await rt._ensure_started()
        aid = AgentId("worker", "1")
        await rt._ensure_agent(aid)
        current = await registry.current(aid)
        assert current is not None
        assert current.worker_id == "worker-A"
        await rt.stop()

    async def test_contention_raises_lease_acquisition_failed(self) -> None:
        registry = InMemoryLeaseRegistry()
        a = LocalRuntime(lease_registry=registry, worker_id="worker-A")
        b = LocalRuntime(lease_registry=registry, worker_id="worker-B")
        await a.register("worker", _noop_handler)
        await b.register("worker", _noop_handler)
        await a._ensure_started()
        await b._ensure_started()
        aid = AgentId("worker", "1")
        await a._ensure_agent(aid)
        with pytest.raises(LeaseAcquisitionFailed) as exc:
            await b._ensure_agent(aid)
        assert exc.value.current_holder_worker_id == "worker-A"
        await a.stop()
        await b.stop()

    async def test_stop_releases_held_leases(self) -> None:
        registry = InMemoryLeaseRegistry()
        rt = LocalRuntime(lease_registry=registry, worker_id="worker-A")
        await rt.register("worker", _noop_handler)
        await rt._ensure_started()
        aid = AgentId("worker", "1")
        await rt._ensure_agent(aid)
        assert await registry.current(aid) is not None
        await rt.stop()
        assert await registry.current(aid) is None

    async def test_hibernate_releases_lease_and_returns_to_dormant(self) -> None:
        registry = InMemoryLeaseRegistry()
        rt = LocalRuntime(lease_registry=registry, worker_id="worker-A")
        await rt.register("worker", _noop_handler)
        await rt._ensure_started()
        aid = AgentId("worker", "1")
        await rt._ensure_agent(aid)
        assert rt.lifecycle_state(aid) is AgentLifecycleState.ACTIVE
        await rt.hibernate(aid)
        assert rt.lifecycle_state(aid) is AgentLifecycleState.DORMANT
        assert await registry.current(aid) is None
        await rt.stop()


class TestLocalRuntimeLifecycleStates:
    async def test_dormant_before_activation(self) -> None:
        rt = LocalRuntime()
        await rt.register("worker", _noop_handler)
        aid = AgentId("worker", "1")
        assert rt.lifecycle_state(aid) is AgentLifecycleState.DORMANT
        assert rt.activation_contract(aid) is None

    async def test_active_after_ensure_agent(self) -> None:
        rt = LocalRuntime()
        await rt.register("worker", _noop_handler)
        await rt._ensure_started()
        aid = AgentId("worker", "1")
        await rt._ensure_agent(aid)
        assert rt.lifecycle_state(aid) is AgentLifecycleState.ACTIVE
        contract = rt.activation_contract(aid)
        assert contract is not None
        assert contract.lifecycle_state is AgentLifecycleState.ACTIVE
        assert contract.trigger is not None
        await rt.stop()


# ---------------------------------------------------------------------------
# Partition affinity
# ---------------------------------------------------------------------------


class TestPartitionAffinity:
    async def test_partition_key_routes_to_matching_instance_only(self) -> None:
        rt = LocalRuntime()
        delivered: dict[str, list[str]] = {"alpha": [], "beta": []}

        async def handler(ctx: MessageContext, payload: Any) -> None:
            key = ctx.agent_id.key
            delivered[key].append(payload[0].text)

        topic = TopicId("events", "stream")
        await rt.register("partitioned", handler)
        await rt.subscribe("partitioned", topic)
        await rt._ensure_started()

        # Pre-activate both partition keys
        await rt._ensure_agent(AgentId("partitioned", "alpha"))
        await rt._ensure_agent(AgentId("partitioned", "beta"))

        # Send a partition-keyed publish: only "alpha" should see it.
        env = Envelope(
            sender=None,
            target=topic,
            content=[TextBlock(text="hello-alpha")],
            locality=LocalityHint(partition_key="alpha"),
        )
        await rt.dispatcher.dispatch(env)
        await rt.stop_when_idle(poll_interval=0.01)

        assert delivered["alpha"] == ["hello-alpha"]
        assert delivered["beta"] == []

    async def test_no_partition_key_broadcasts_to_all(self) -> None:
        rt = LocalRuntime()
        delivered: dict[str, list[str]] = {"alpha": [], "beta": []}

        async def handler(ctx: MessageContext, payload: Any) -> None:
            delivered[ctx.agent_id.key].append(payload[0].text)

        topic = TopicId("events", "stream")
        await rt.register("partitioned", handler)
        await rt.subscribe("partitioned", topic)
        await rt._ensure_started()
        await rt._ensure_agent(AgentId("partitioned", "alpha"))
        await rt._ensure_agent(AgentId("partitioned", "beta"))

        # No partition_key → broadcast to every instance.
        env = Envelope(
            sender=None,
            target=topic,
            content=[TextBlock(text="broadcast")],
        )
        await rt.dispatcher.dispatch(env)
        await rt.stop_when_idle(poll_interval=0.01)

        assert delivered["alpha"] == ["broadcast"]
        assert delivered["beta"] == ["broadcast"]
