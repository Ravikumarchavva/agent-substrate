"""Tests for fabric.durable — DurableRunner and InMemoryCheckpointStore."""

from __future__ import annotations


from ravi.agents.middleware import AgentRunResult
from ravi.fabric.durable import (
    DurableRunner,
    FlowCheckpoint,
    InMemoryCheckpointStore,
)
from ravi.fabric.flows import SequentialFlow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class StubStep:
    def __init__(self, name: str, reply: str = "ok"):
        self.name = name
        self.reply = reply
        self.call_count = 0

    async def run(self, input_text: str, **kwargs) -> AgentRunResult:
        self.call_count += 1
        return AgentRunResult(
            output=self.reply,
            status="success",
            run_id="step-run",
        )

    async def run_stream(self, input_text: str, **kwargs):
        # Needed so SequentialFlow typechecks; not exercised in these tests.
        if False:
            yield


def _two_step_flow() -> tuple[SequentialFlow, StubStep, StubStep]:
    a = StubStep(name="step_a", reply="A")
    b = StubStep(name="step_b", reply="B")
    flow = SequentialFlow(steps=[a, b], name="test_flow")
    return flow, a, b


# ---------------------------------------------------------------------------
# InMemoryCheckpointStore
# ---------------------------------------------------------------------------

async def test_store_save_and_load_round_trip():
    store = InMemoryCheckpointStore()
    cp = FlowCheckpoint(run_id="r1", flow_id="f1", step_index=2, state={"k": "v"})
    await store.save(cp)
    loaded = await store.load("r1", "f1")
    assert loaded is not None
    assert loaded.step_index == 2
    assert loaded.state["k"] == "v"


async def test_store_load_returns_none_for_missing():
    store = InMemoryCheckpointStore()
    result = await store.load("no_such_run", "no_such_flow")
    assert result is None


async def test_store_overwrite_on_resave():
    store = InMemoryCheckpointStore()
    cp1 = FlowCheckpoint(run_id="r1", flow_id="f1", step_index=0)
    cp2 = FlowCheckpoint(run_id="r1", flow_id="f1", step_index=1)
    await store.save(cp1)
    await store.save(cp2)
    loaded = await store.load("r1", "f1")
    assert loaded.step_index == 1


# ---------------------------------------------------------------------------
# DurableRunner — fresh run
# ---------------------------------------------------------------------------

async def test_durable_runner_fresh_run_executes_all_steps():
    flow, a, b = _two_step_flow()
    runner = DurableRunner(flow)
    result = await runner.run("start")

    assert result.status == "success"
    assert result.output == "B"
    assert a.call_count == 1
    assert b.call_count == 1


async def test_durable_runner_saves_checkpoint_after_each_step():
    store = InMemoryCheckpointStore()
    flow, a, b = _two_step_flow()
    runner = DurableRunner(flow, store=store)
    await runner.run("start", run_id="my-run")

    # After completion the checkpoint should be at the last step index (1)
    cp = await store.load("my-run", "test_flow")
    assert cp is not None
    assert cp.step_index == 1  # last step completed


async def test_durable_runner_run_id_propagated():
    flow, _, _ = _two_step_flow()
    runner = DurableRunner(flow)
    result = await runner.run("x", run_id="explicit-id")
    assert result.run_id == "explicit-id"


# ---------------------------------------------------------------------------
# DurableRunner — resume
# ---------------------------------------------------------------------------

async def test_durable_runner_resume_skips_completed_steps():
    store = InMemoryCheckpointStore()
    # Pre-populate checkpoint: step 0 already done
    cp = FlowCheckpoint(
        run_id="resume-run",
        flow_id="test_flow",
        step_index=0,
        state={"accumulated": "start\n\nA"},
    )
    await store.save(cp)

    flow, a, b = _two_step_flow()
    runner = DurableRunner(flow, store=store)
    result = await runner.resume("resume-run", input_text="start")

    # step_a should NOT have been called — we resumed from step 0
    assert a.call_count == 0
    # step_b MUST have been called
    assert b.call_count == 1
    assert result.output == "B"


async def test_durable_runner_resume_with_no_checkpoint_runs_all():
    """resume() with no existing checkpoint just runs normally."""
    store = InMemoryCheckpointStore()
    flow, a, b = _two_step_flow()
    runner = DurableRunner(flow, store=store)
    result = await runner.resume("brand-new-run", input_text="hi")

    assert a.call_count == 1
    assert b.call_count == 1
    assert result.status == "success"
