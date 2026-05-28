"""End-to-end Section 4 proof: two DistributedRuntime workers, one fabric.

A shared :class:`InMemoryLeaseRegistry` decides who hosts each agent. A
shared :class:`InMemoryEventFabric` carries forward / reply envelopes
between workers. Combined, two workers can transparently forward a
:meth:`send_message` call to whichever one currently holds the lease.

These tests are the proof that the kernel contracts compose:

* The lease registry assigns ownership.
* The envelope carries identity / trust / placement through the wire.
* The fabric delivers the wire envelope to the holding worker.
* The holding worker dispatches locally and replies.
* The originating worker resolves its outstanding future.
"""

from __future__ import annotations

from typing import Any

import pytest

from ravi.fabric.events import InMemoryEventFabric
from ravi.fabric.runtime import DistributedRuntime
from ravi.kernel.runtime import (
    AgentId,
    AgentLifecycleState,
    InMemoryLeaseRegistry,
    MessageContext,
)


async def _echo_handler(ctx: MessageContext, payload: Any) -> str:
    return f"echo:{payload[0].text}"


class TestSingleWorker:
    async def test_local_send_when_no_contention(self) -> None:
        fabric = InMemoryEventFabric()
        registry = InMemoryLeaseRegistry()
        rt = DistributedRuntime(
            fabric=fabric, lease_registry=registry, worker_id="A"
        )
        await rt.register("echo", _echo_handler)
        result = await rt.send_message(
            "hello", recipient=AgentId("echo", "1")
        )
        assert result == "echo:hello"
        # Worker A owns it now
        assert rt.lifecycle_state(AgentId("echo", "1")) is AgentLifecycleState.ACTIVE
        await rt.stop()


class TestCrossWorkerHandoff:
    async def test_second_worker_forwards_through_fabric(self) -> None:
        fabric = InMemoryEventFabric()
        registry = InMemoryLeaseRegistry()
        a = DistributedRuntime(
            fabric=fabric,
            lease_registry=registry,
            worker_id="A",
            remote_send_timeout=2.0,
        )
        b = DistributedRuntime(
            fabric=fabric,
            lease_registry=registry,
            worker_id="B",
            remote_send_timeout=2.0,
        )
        await a.register("echo", _echo_handler)
        await b.register("echo", _echo_handler)

        # Worker A claims the agent first.
        first = await a.send_message("ping", recipient=AgentId("echo", "1"))
        assert first == "echo:ping"
        assert a.lifecycle_state(AgentId("echo", "1")) is AgentLifecycleState.ACTIVE

        # Worker B sends to the same agent — must forward to A.
        result = await b.send_message("pong", recipient=AgentId("echo", "1"))
        # Reply carries the repr of the remote result.
        assert "echo:pong" in result

        # Worker B never activated the agent locally.
        assert (
            b.lifecycle_state(AgentId("echo", "1")) is AgentLifecycleState.DORMANT
        )

        await a.stop()
        await b.stop()

    async def test_handoff_after_hibernation(self) -> None:
        fabric = InMemoryEventFabric()
        registry = InMemoryLeaseRegistry()
        a = DistributedRuntime(
            fabric=fabric, lease_registry=registry, worker_id="A"
        )
        b = DistributedRuntime(
            fabric=fabric, lease_registry=registry, worker_id="B"
        )
        await a.register("echo", _echo_handler)
        await b.register("echo", _echo_handler)

        aid = AgentId("echo", "1")
        await a.send_message("hello", recipient=aid)
        assert a.lifecycle_state(aid) is AgentLifecycleState.ACTIVE

        # Worker A hibernates the agent → lease released.
        await a.hibernate(aid)
        assert a.lifecycle_state(aid) is AgentLifecycleState.DORMANT
        assert await registry.current(aid) is None

        # Now worker B can host it locally.
        result = await b.send_message("from-B", recipient=aid)
        assert result == "echo:from-B"
        assert b.lifecycle_state(aid) is AgentLifecycleState.ACTIVE

        await a.stop()
        await b.stop()


class TestErrorPropagation:
    async def test_remote_handler_error_surfaces(self) -> None:
        fabric = InMemoryEventFabric()
        registry = InMemoryLeaseRegistry()

        async def boom(ctx: MessageContext, payload: Any) -> str:
            raise ValueError("boom from remote")

        a = DistributedRuntime(
            fabric=fabric,
            lease_registry=registry,
            worker_id="A",
            remote_send_timeout=2.0,
            send_timeout=2.0,
        )
        b = DistributedRuntime(
            fabric=fabric,
            lease_registry=registry,
            worker_id="B",
            remote_send_timeout=2.0,
            send_timeout=2.0,
        )
        await a.register("flaky", boom)
        await b.register("flaky", boom)

        # A claims the agent via a successful local call (boom raises but
        # the agent is still leased on A).
        aid = AgentId("flaky", "1")
        with pytest.raises(Exception):
            await a.send_message("x", recipient=aid)

        # B sends — forwarded to A. A's handler raises → reply carries
        # error_repr → B's future resolves with RuntimeError.
        with pytest.raises(Exception):
            await b.send_message("y", recipient=aid)

        await a.stop()
        await b.stop()
