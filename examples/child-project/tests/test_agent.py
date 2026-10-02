"""The acceptance tests: everything a downstream project does with the library, using only what it exports."""

from __future__ import annotations

import asyncio

import pytest
from substrate import ApprovalDecision, DurableApproval, Runtime, ToolRisk
from substrate.stores import Store
from substrate.testing.conformance.thread_store import ThreadStoreConformance
from substrate.types import RunLogKind

from ticket_desk.app import build_agent
from ticket_desk.threads import AuditedThreads
from ticket_desk.tools import TICKETS, close_ticket, lookup_ticket


@pytest.fixture(autouse=True)
def _fresh_tickets():
    before = {k: dict(v) for k, v in TICKETS.items()}
    yield
    TICKETS.clear()
    TICKETS.update(before)


async def test_the_agent_answers_from_its_tool(tmp_path):
    audit: list[str] = []
    store = Store.at(tmp_path)
    async with Runtime(store) as runtime:
        outcome = await runtime.run(build_agent(store, audit), "What is the state of T-100?", thread="alice")
    await store.aclose()
    assert "Printer is on fire" in outcome.output
    assert audit, "our ThreadStore was never used"


async def test_a_risky_tool_waits_for_a_person_and_the_decision_is_journaled(tmp_path):
    audit: list[str] = []
    store = Store.at(tmp_path)
    async with Runtime(store) as runtime:
        agent = build_agent(store, audit, approval=DurableApproval())
        work = asyncio.create_task(runtime.run(agent, "Please close T-101", thread="bob"))
        run, pending = None, []
        while not pending:
            await asyncio.sleep(0.02)
            run = await runtime.active_run_for_thread("bob")
            pending = await runtime.pending_approvals(run.run_id) if run else []
        assert (pending[0].tool_name, pending[0].risk) == ("close_ticket", "high")
        assert TICKETS["T-101"]["status"] == "open"  # nothing happened while it waited
        await runtime.decide(run.run_id, pending[0].request_id, ApprovalDecision.APPROVED, by="carol")
        outcome = await work
        decided = [e.payload for e in await runtime.read(run.run_id) if e.kind == RunLogKind.APPROVAL_DECIDED]
    await store.aclose()
    assert TICKETS["T-101"]["status"] == "closed (fixed)" and "closed" in outcome.output
    assert decided[0]["decided_by"] == "carol"


async def test_the_default_policy_denies_what_it_may_not_approve(tmp_path):
    from substrate.tools import AutoApprove

    store = Store.at(tmp_path)
    async with Runtime(store) as runtime:
        agent = build_agent(store, [], approval=AutoApprove(ToolRisk.SAFE))
        await runtime.run(agent, "Please close T-101", thread="dave")
    await store.aclose()
    assert TICKETS["T-101"]["status"] == "open"


async def test_a_tool_is_still_a_function_and_declares_what_the_engine_acts_on():
    assert lookup_ticket.risk is ToolRisk.SAFE and lookup_ticket.idempotent
    assert close_ticket.risk is ToolRisk.HIGH
    assert close_ticket.input_schema["properties"]["resolution"]["enum"] == ["fixed", "wont_fix", "duplicate"]
    assert (await lookup_ticket.execute(ticket_id="T-100")).text.count("Printer") == 1


class TestAuditedThreadsIsAThreadStore(ThreadStoreConformance):
    """Our wrapper held to the library's own suite for the port: if it passes, the engine can use it anywhere."""

    @pytest.fixture
    async def store(self, tmp_path):
        inner = Store.at(tmp_path / "s")
        yield AuditedThreads(inner.threads, [])
        await inner.aclose()


async def test_the_agent_is_served_over_http_and_ag_ui_with_one_call(tmp_path):
    """``create_app`` takes the agent object; the same run is available as the engine's SSE and as AG-UI."""
    import json

    from fastapi.testclient import TestClient
    from substrate.server import create_app

    store = Store.at(tmp_path)
    app = create_app(build_agent(store, []), store=store)

    def frames(text):
        return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ") and "[DONE]" not in line]

    with TestClient(app) as client:
        wire = frames(client.post("/chat", json={"message": "What is the state of T-100?", "thread_id": "w"}).text)
        agui = frames(
            client.post("/agui", json={"threadId": "a", "runId": "r", "messages": [{"id": "1", "role": "user", "content": "What is the state of T-101?"}]}).text
        )
    assert wire[-1]["type"] == "run.completed" and any(e["type"] == "tool.call" for e in wire)
    assert [e["type"] for e in agui][0] == "RUN_STARTED" and [e["type"] for e in agui][-1] == "RUN_FINISHED"
    assert any(e["type"] == "TOOL_CALL_START" and e["toolCallName"] == "lookup_ticket" for e in agui)
    assert any("Cannot log in" in e.get("delta", "") or "Cannot log in" in e.get("content", "") for e in agui)
