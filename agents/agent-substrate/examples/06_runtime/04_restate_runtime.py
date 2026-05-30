"""Restate durable execution — setup, usage, and graceful fallback.

Restate (https://restate.dev) is a durable workflow engine.  When agents
are invoked via Restate, every step is journaled — if the process crashes
mid-execution, Restate replays the journal and the agent picks up exactly
where it left off.

This file demonstrates:
1. Settings that control Restate integration (RESTATE_INGRESS_URL, etc.)
2. How the SagaCoordinator (built into LocalRuntime) provides the same
   exactly-once semantics locally — the pattern Restate generalises
3. Durable multi-step workflow with compensating rollback
4. Graceful fallback when Restate is not running

Infrastructure: Restate server (optional — demo runs without it).
  Start with: docker run --rm -p 8080:8080 -p 9070:9070 docker.restate.dev/restatedev/restate
"""

from __future__ import annotations

import asyncio
import uuid

from ravi.config import settings
from ravi.kernel.runtime import (
    RestartPolicy,
)
from ravi.fabric.runtime import LocalRuntime
from ravi.fabric.actors.actor import ActorAgent
from ravi.fabric import (
    CheckpointStatus,
    InMemoryCheckpointStore,
    RunCheckpoint,
    SagaCoordinator,
    SagaFailedError,
)
from ravi.kernel.runtime._contracts import MessageContext
from ravi.kernel.messages.content import ContentBlock

# ---
# Restate integration settings
#
#   RESTATE_INGRESS_URL  (default: http://localhost:8080)
#       HTTP endpoint where agents receive durable invocations.
#       Agents are registered with Restate admin and invoked via this URL.
#
#   RESTATE_ADMIN_URL    (default: http://localhost:9070)
#       Admin endpoint for registering agent endpoints and querying state.
#
#   NATS_URL             (default: nats://localhost:4222)
#       NATS JetStream endpoint used by DistributedRuntime for cross-worker
#       pub/sub (independent of Restate).
#
# These live in ravi.config and can be overridden in .env.
# ---


# --- Section 1: show settings ---


def show_settings() -> None:
    print("=== Restate / distributed runtime settings ===")
    print(f"  RESTATE_INGRESS_URL: {settings.RESTATE_INGRESS_URL}")
    print(f"  RESTATE_ADMIN_URL:   {settings.RESTATE_ADMIN_URL}")
    print(f"  NATS_URL:            {settings.NATS_URL}")


# --- Section 2: SagaCoordinator — exactly-once local execution ---


async def demo_saga_coordinator() -> None:
    print("\n=== SagaCoordinator — exactly-once local steps ===")

    # ---
    # SagaCoordinator is the LOCAL equivalent of Restate's durable execution:
    #
    #   - Each step is identified by a stable step_id
    #   - If the process crashes after step 1 succeeds but before step 2
    #     starts, recovery replays: step 1 is SKIPPED (result from store),
    #     step 2 runs fresh
    #   - If step 2 fails, step 1's compensating action (refund/cancel) runs
    #
    # Restate extends this to distributed processes via its journal + HTTP ingress.
    # ---

    saga = SagaCoordinator()  # uses in-memory store (default)

    # Simulate side-effectful actions with compensations
    payment_charged = False
    hotel_booked = False
    payment_refunded = False
    hotel_cancelled = False

    async def charge_payment() -> dict:
        nonlocal payment_charged
        payment_charged = True
        charge_id = f"ch_{uuid.uuid4().hex[:8]}"
        print(f"    charged payment: {charge_id}")
        return {"charge_id": charge_id}

    async def refund_payment(result: dict) -> None:
        nonlocal payment_refunded
        payment_refunded = True
        print(f"    refunded payment: {result['charge_id']}")

    async def book_hotel() -> dict:
        nonlocal hotel_booked
        hotel_booked = True
        booking_id = f"bk_{uuid.uuid4().hex[:8]}"
        print(f"    booked hotel: {booking_id}")
        return {"booking_id": booking_id}

    async def cancel_hotel(result: dict) -> None:
        nonlocal hotel_cancelled
        hotel_cancelled = True
        print(f"    cancelled hotel: {result['booking_id']}")

    async def confirm_order() -> dict:
        raise RuntimeError("order system down")  # step 3 fails

    # Run saga — steps 1+2 succeed, step 3 fails → compensation runs
    print("  Running saga (step 3 will fail):")
    try:
        async with saga.begin("order-demo-001") as ctx:
            charge = await ctx.step(
                step_id="charge-card",
                action=charge_payment,
                compensate=lambda result: refund_payment(result),
            )
            booking = await ctx.step(
                step_id="book-hotel",
                action=book_hotel,
                compensate=lambda result: cancel_hotel(result),
            )
            await ctx.step(
                step_id="confirm-order",
                action=confirm_order,
            )
    except (SagaFailedError, RuntimeError) as exc:
        print(f"  Saga failed (expected): {type(exc).__name__}")

    print(f"  payment_charged:   {payment_charged}")
    print(f"  hotel_booked:      {hotel_booked}")
    print(f"  payment_refunded:  {payment_refunded}  (compensation ran)")
    print(f"  hotel_cancelled:   {hotel_cancelled}  (compensation ran)")


# --- Section 3: CheckpointStore — tree-structured execution snapshots ---


async def demo_checkpoints() -> None:
    print("\n=== CheckpointStore — durable execution snapshots ===")

    # ---
    # RunCheckpoint is a tree: the orchestrator is the root, sub-agents
    # it spawns are children.  On recovery:
    #   - completed children → use stored result (skip re-execution)
    #   - in_progress children → re-run from scratch
    #   - not_started children → run normally
    #
    # InMemoryCheckpointStore ships built-in.
    # Production: swap for RedisCheckpointStore or S3CheckpointStore.
    # ---

    store = InMemoryCheckpointStore()

    run_id = f"run_{uuid.uuid4().hex[:8]}"

    # Create root checkpoint (orchestrator)
    root = RunCheckpoint(
        run_id=run_id,
        agent_id="orchestrator",
        status=CheckpointStatus.IN_PROGRESS,
        iteration=0,
    )
    root.mark_in_progress(iteration=1)

    # Add child for research sub-agent (completed)
    research = RunCheckpoint(run_id=run_id, agent_id="research_agent")
    research.mark_completed(result={"findings": "Python 3.13 released"})
    root.add_child(research)

    # Add child for writer sub-agent (in-progress — simulating a crash)
    writer = RunCheckpoint(run_id=run_id, agent_id="writer_agent")
    writer.mark_in_progress(iteration=2)
    root.add_child(writer)

    # Persist
    await store.save(root)

    # Simulate recovery
    recovered = await store.load(run_id=run_id, agent_id="orchestrator")
    assert recovered is not None

    print(f"  run_id:           {recovered.run_id}")
    print(f"  orchestrator:     {recovered.status.value}")
    print(f"  needs_recovery:   {recovered.needs_recovery}")
    print("  children:")
    for child in recovered.children:
        needs = " (needs re-run)" if child.is_in_progress else " (skip — cached)"
        print(f"    {child.agent_id}: {child.status.value}{needs}")

    completed = recovered.completed_children
    incomplete = recovered.incomplete_children
    print(f"  completed_children:  {[c.agent_id for c in completed]}")
    print(f"  incomplete_children: {[c.agent_id for c in incomplete]}")
    print(f"  research result:     {recovered.find_child('research_agent').result}")


# --- Section 4: graceful fallback when Restate is not running ---


async def demo_restate_fallback() -> None:
    print("\n=== Restate connection — graceful fallback ===")

    ingress_url = settings.RESTATE_INGRESS_URL
    print(f"  RESTATE_INGRESS_URL: {ingress_url}")

    try:
        import aiohttp  # noqa: F401

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    f"{ingress_url}/restate/health",
                    timeout=aiohttp.ClientTimeout(total=2),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        print(f"  Restate is healthy: {data}")
                    else:
                        print(f"  Restate returned HTTP {resp.status}")
            except Exception as exc:
                print(
                    f"  Restate not reachable ({type(exc).__name__}) — using local fallback."
                )
                print(
                    "  To start Restate: docker run --rm -p 8080:8080 -p 9070:9070 \\"
                )
                print("    docker.restate.dev/restatedev/restate")
    except ImportError:
        print("  aiohttp not installed — checking via urllib instead.")
        import urllib.request

        try:
            with urllib.request.urlopen(
                f"{ingress_url}/restate/health", timeout=2
            ) as resp:
                print(f"  Restate healthy: {resp.read().decode()[:80]}")
        except Exception as exc:
            print(
                f"  Restate not reachable ({type(exc).__name__}) — using local fallback."
            )
            print(
                "  LocalRuntime + SagaCoordinator provide the same guarantees in-process."
            )


# --- Section 5: LocalRuntime with SagaCoordinator — the local equivalent ---


async def demo_local_durable_agent() -> None:
    print("\n=== Durable agent via LocalRuntime + SagaCoordinator ===")

    # ---
    # When Restate is not available, LocalRuntime.saga_coordinator provides
    # exactly-once step execution within a single process restart.
    # Swap LocalRuntime for a Restate-backed runtime when deploying to
    # production for cross-process durability.
    # ---

    runtime = LocalRuntime(
        restart_policy=RestartPolicy(max_restarts=3, restart_window=60.0),
        send_timeout=10.0,
    )
    await runtime.start()

    class DurableWorkflowAgent(ActorAgent):
        """Runs a multi-step workflow with exactly-once semantics."""

        async def on_message(
            self, ctx: MessageContext, content: list[ContentBlock]
        ) -> object:
            task_id = (
                content[0].text
                if content and hasattr(content[0], "text")
                else "unknown"
            )
            saga = runtime.saga_coordinator

            results = {}

            async def fetch_data() -> dict:
                return {"data": f"fetched for {task_id}"}

            async def process_data() -> dict:
                return {"processed": True}

            async with saga.begin(f"workflow-{task_id}") as wf:
                results["step1"] = await wf.step(
                    step_id=f"{task_id}-fetch",
                    action=fetch_data,
                )
                results["step2"] = await wf.step(
                    step_id=f"{task_id}-process",
                    action=process_data,
                )

            return {"task_id": task_id, "results": results, "status": "completed"}

    agent = DurableWorkflowAgent(name="durable_workflow", runtime=runtime)
    await agent.start()

    result = await runtime.send_message("task-abc-123", recipient=agent.id)
    print(f"  workflow result: {result}")
    print(f"  saga_coordinator: {runtime.saga_coordinator!r}")

    await agent.stop()
    await runtime.stop()


async def main() -> None:
    show_settings()
    await demo_saga_coordinator()
    await demo_checkpoints()
    await demo_restate_fallback()
    await demo_local_durable_agent()
    print("\nRestate / durable execution demos complete.")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
