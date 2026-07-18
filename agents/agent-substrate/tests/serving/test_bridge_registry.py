"""BridgeRegistry durable HITL resolution — the cross-replica fallback path.

A request_id with no local bridge (this "replica" never tailed the
input.requested event for that thread) must still resolve via
SchedulerProtocol.find_run_by_wake_signal(), not silently fail.
"""

from __future__ import annotations

from substrate.agents.runtime.backends._scheduler import InMemoryScheduler
from substrate.agents.runtime.backends._signal_bus import InMemorySignalBus
from substrate.kernel.core.identity import AgentId
from substrate.kernel.runtime.ids import RunStatus
from substrate.kernel.runtime.wakeup import Wakeup
from substrate.serving.monolith.sse.bridge import BridgeRegistry


async def test_resolve_falls_back_to_durable_lookup_when_no_local_bridge() -> None:
    scheduler = InMemoryScheduler()
    signal_bus = InMemorySignalBus(scheduler)

    run_id = "run-durable-hitl"
    request_id = "req-123"
    scheduler.register_run(run_id, AgentId(type="agent", key="x"))
    await scheduler.enqueue(run_id, priority=5, tenant="default")
    # Simulate the run having suspended waiting on this HITL signal — same
    # state Worker._run_agent would leave behind via release(SUSPENDED, ...).
    scheduler._status[run_id] = RunStatus.SUSPENDED
    scheduler._wakeups[run_id] = Wakeup(kind="signal", signals=[f"hitl:{request_id}"])

    registry = BridgeRegistry(signal_bus=signal_bus, scheduler=scheduler)
    # No bridge for any thread was ever acquired — this replica has zero
    # local knowledge of request_id, exactly the cross-replica scenario.
    ok = await registry.resolve(request_id, {"answer": "yes"})
    assert ok is True

    payload = await signal_bus.consume(run_id, f"hitl:{request_id}", "test-effect-id")
    assert payload == {"answer": "yes"}


async def test_resolve_returns_false_when_truly_unknown() -> None:
    scheduler = InMemoryScheduler()
    signal_bus = InMemorySignalBus(scheduler)
    registry = BridgeRegistry(signal_bus=signal_bus, scheduler=scheduler)

    ok = await registry.resolve("nonexistent-request-id", {})
    assert ok is False
