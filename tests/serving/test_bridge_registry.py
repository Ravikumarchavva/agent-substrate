"""BridgeRegistry durable HITL resolution — the cross-replica fallback path.

A request_id with no local bridge (this "replica" never tailed the input.requested event
for that thread) must still resolve by finding the run waiting on it in the store.
"""

from __future__ import annotations

from datetime import datetime, timezone

from substrate.kernel.abstractions.core.identity import Actor
from substrate.kernel.abstractions.runtime.store import Commit, RunSpec, Suspend
from substrate.kernel.abstractions.runtime.wakeup import Wakeup
from substrate.kernel.runtime.sqlite_store import SqliteRuntimeStore
from substrate.serving.monolith.sse.bridge import BridgeRegistry


async def test_resolve_falls_back_to_durable_lookup_when_no_local_bridge() -> None:
    store = SqliteRuntimeStore(":memory:")
    await store.start()
    try:
        run = await store.create_run(RunSpec(agent=Actor(type="agent", key="x")))
        (lease,) = await store.lease(worker_id="w1", capacity=1, lease_s=30, now=datetime.now(timezone.utc))
        request_id = "req-123"
        # The run has suspended waiting on this HITL signal.
        await store.commit(lease, Commit(outcome=Suspend(wake=Wakeup(kind="signal", signals=[f"hitl:{request_id}"]))))

        registry = BridgeRegistry(store=store)
        # No bridge for any thread was ever acquired — this replica has zero local
        # knowledge of request_id, exactly the cross-replica scenario.
        assert await registry.resolve(request_id, {"answer": "yes"}) is True

        payload = await store.consume(run.run_id, f"hitl:{request_id}", "test-effect-id")
        assert payload == {"answer": "yes"}
    finally:
        await store.aclose()


async def test_resolve_returns_false_when_truly_unknown() -> None:
    store = SqliteRuntimeStore(":memory:")
    await store.start()
    try:
        assert await BridgeRegistry(store=store).resolve("nonexistent-request-id", {}) is False
    finally:
        await store.aclose()
