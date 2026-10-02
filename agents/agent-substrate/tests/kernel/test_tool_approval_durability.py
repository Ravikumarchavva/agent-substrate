"""Tool-approval HITL is durable: a pending CRITICAL/HIGH-risk approval survives a process
restart, the same way ask_human does.

Two runtimes on one SQLite file stand in for two process lifetimes: the first reaches the
approval and is stopped, the human answers while nothing is running, and a second runtime
with brand-new agent objects picks the run up and finishes it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from substrate.types import TextBlock
from substrate.types import Actor
from substrate.runtime import DataPayload, Message
from substrate.types import RunLogKind
from substrate.runtime import Delivery
from substrate.tools import ToolExecutionResult, ToolRisk
from substrate.tools import ApprovalRequest, ApprovalResult
from substrate.runtime import Runtime
from substrate.tools import Toolbox

import pytest  # noqa: F401
from tests.test_examples import EXAMPLES

TIMEOUT = 10


class SendEmailTool:
    name = "send_email"
    description = "Sends an email."
    risk = ToolRisk.HIGH
    idempotent = False
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"to": {"type": "string"}},
    }

    async def execute(self, *, ctx: Any = None, **kwargs: Any) -> ToolExecutionResult:
        return ToolExecutionResult(
            name=self.name,
            content=[TextBlock(text=f"email sent to {kwargs.get('to')}")],
        )


class SignalApprovalHandler:
    """Marks itself signal-capable, as the web approval handler does. ``request()`` must
    never be called: if it is, the in-memory fallback fired instead of the durable path."""

    suspends_via_signal = True

    async def request(self, req: ApprovalRequest) -> ApprovalResult:
        raise AssertionError("request() called — the durable signal path was not used")


class Mailer:
    """Calls the HIGH-risk tool once and keeps the answer."""

    def __init__(self) -> None:
        self.id = Actor("agent", "mailer")
        self.tools = Toolbox()
        self.tools.add(SendEmailTool())
        self.approval_handler = SignalApprovalHandler()
        self.results: list[Any] = []

    async def run(self, ctx: Any, inbox: list[Message]) -> None:
        self.results.append(await ctx.tool("send_email", {"to": "user@example.com"}))


def _boot(agent: Mailer) -> Message:
    return Message(
        target=agent.id, sender=Actor.system("test"), payload=DataPayload(data={})
    )


async def _wait_for(rt: Runtime, run_id: str, kind: str) -> dict[str, Any]:
    async def watch() -> dict[str, Any]:
        async for entry in rt.tail(run_id):
            if entry.kind == kind:
                return dict(entry.payload or {})
        raise AssertionError(f"tail ended before {kind}")

    return await asyncio.wait_for(watch(), TIMEOUT)


async def _approve_across_restart(path: Path, action: str) -> Mailer:
    first = Mailer()
    async with Runtime.open(path) as rt:
        await rt.register(first)
        run_id = await rt.submit(first.id, _boot(first))
        request = await _wait_for(rt, run_id, RunLogKind.APPROVAL_REQUESTED)
        # Let the suspension commit before the "process" goes away.
        for _ in range(200):
            if (await rt.get_run(run_id)).status == "suspended":
                break
            await asyncio.sleep(0.01)

    # The human responds while nothing is running.
    async with Runtime.open(path) as rt:
        await rt.store.signal(
            run_id, f"hitl:{request['request_id']}", {"action": action}
        )
        second = Mailer()
        await rt.register(second)
        await _wait_for(rt, run_id, RunLogKind.RUN_COMPLETED)
    return second


async def test_tool_approval_survives_restart_and_resumes_when_approved(
    tmp_path: Path,
) -> None:
    second = await _approve_across_restart(tmp_path / "rt.sqlite3", "approve")

    (result,) = second.results
    assert result.status == "ok"
    assert "user@example.com" in (result.text or "")


async def test_tool_approval_survives_restart_and_denies_when_rejected(
    tmp_path: Path,
) -> None:
    second = await _approve_across_restart(tmp_path / "rt.sqlite3", "deny")

    (result,) = second.results
    assert result.status == "denied"


async def test_tool_approval_request_id_is_replay_stable(tmp_path: Path) -> None:
    """A replayed suspend (no answer yet) must reuse the identical request_id and not write
    a second approval.requested — otherwise every retry would orphan the previous card."""
    path = tmp_path / "rt.sqlite3"
    agent = Mailer()
    async with Runtime.open(path) as rt:
        await rt.register(agent)
        run_id = await rt.submit(agent.id, _boot(agent))
        first = await _wait_for(rt, run_id, RunLogKind.APPROVAL_REQUESTED)
        for _ in range(200):
            if (await rt.get_run(run_id)).status == "suspended":
                break
            await asyncio.sleep(0.01)

    async with Runtime.open(path) as rt:
        again = Mailer()
        await rt.register(again)
        # A new message wakes the suspended run; it replays to the same approval and suspends again.
        await rt.store.deliver(Delivery(agent=again.id, msg=_boot(again)))
        await asyncio.sleep(0.3)
        assert [e.kind for e in await rt.read(run_id)].count(
            RunLogKind.RUN_RESUMED
        ) >= 1, "the run was not replayed"
        requests = [
            e for e in await rt.read(run_id) if e.kind == RunLogKind.APPROVAL_REQUESTED
        ]

    assert [r.payload["request_id"] for r in requests] == [first["request_id"]]



async def _ask(runtime, agent):
    """Start the agent on a thread and wait until it is waiting on a person."""
    work = asyncio.create_task(runtime.run(agent, "go", thread="t"))
    run = None
    while run is None or not (pending := await runtime.pending_approvals(run.run_id)):
        await asyncio.sleep(0.02)
        run = await runtime.active_run_for_thread("t")
    return work, run, pending


def _agent():
    from substrate.agents import ReActAgent
    from substrate.tools import DurableApproval, tool

    @tool(risk=ToolRisk.CRITICAL, idempotent=False)
    def wire(amount: int) -> str:
        """Wire money."""
        return f"wired {amount}"

    import sys

    sys.path.insert(0, str(EXAMPLES))
    try:
        from _model import ScriptedModel, ToolCall
    finally:
        sys.path.remove(str(EXAMPLES))
    return ReActAgent("t", model=ScriptedModel(ToolCall("wire", {"amount": 5}), "done"), tools=[wire], approval_handler=DurableApproval())


async def test_pending_approvals_and_decide_close_the_loop_without_hand_rolled_signals(tmp_path: Path) -> None:
    """``runtime.pending_approvals`` lists what a run waits on and ``runtime.decide`` answers it, so a person reading the
    list never needs the signal name or its payload; the decision is journaled with who made it."""
    from substrate.tools import ApprovalDecision

    async with Runtime.open(tmp_path / "s") as runtime:
        work, run, pending = await _ask(runtime, _agent())
        assert [(p.tool_name, p.args, p.risk) for p in pending] == [("wire", {"amount": 5}, "critical")]
        await runtime.decide(run.run_id, pending[0].request_id, ApprovalDecision.APPROVED, by="alice", reason="ok")
        assert (await work).output == "done"
        assert await runtime.pending_approvals(run.run_id) == []
        decided = [e.payload for e in await runtime.read(run.run_id) if e.kind == RunLogKind.APPROVAL_DECIDED]
        assert [(d["decision"], d["decided_by"], d["reason"]) for d in decided] == [("approved", "alice", "ok")]


async def test_a_denied_call_never_runs_and_a_modified_one_runs_with_the_edited_arguments(tmp_path: Path) -> None:
    from substrate.tools import ApprovalDecision

    async with Runtime.open(tmp_path / "s") as runtime:
        work, run, pending = await _ask(runtime, _agent())
        await runtime.decide(run.run_id, pending[0].request_id, ApprovalDecision.DENIED, by="bob")
        await work
        results = [e.payload for e in await runtime.read(run.run_id) if e.kind == RunLogKind.TOOL_RESULT]
        assert results and all("wired" not in str(r) for r in results)

    async with Runtime.open(tmp_path / "s2") as runtime:
        work, run, pending = await _ask(runtime, _agent())
        await runtime.decide(
            run.run_id, pending[0].request_id, ApprovalDecision.MODIFIED, by="bob", modified_args={"amount": 1}
        )
        await work
        results = [str(e.payload) for e in await runtime.read(run.run_id) if e.kind == RunLogKind.TOOL_RESULT]
        assert any("wired 1" in r for r in results)


async def test_a_decision_a_person_cannot_make_is_refused(tmp_path: Path) -> None:

    from substrate.tools import ApprovalDecision

    async with Runtime.open(tmp_path / "s") as runtime:
        with pytest.raises(ValueError):
            await runtime.decide("r", "q", ApprovalDecision.SKIPPED, by="x")
        with pytest.raises(ValueError, match="modified_args"):
            await runtime.decide("r", "q", ApprovalDecision.MODIFIED, by="x")


async def test_auto_approve_allows_up_to_its_ceiling_and_says_why() -> None:
    from substrate.tools import ApprovalDecision, ApprovalRequest, AutoApprove, ToolCallRequest

    policy = AutoApprove(ToolRisk.HIGH)
    for risk, decision in ((ToolRisk.SAFE, ApprovalDecision.APPROVED), (ToolRisk.HIGH, ApprovalDecision.APPROVED), (ToolRisk.CRITICAL, ApprovalDecision.DENIED)):
        result = await policy.request(
            ApprovalRequest(call=ToolCallRequest(name="t"), risk=risk, agent_id=Actor("agent", "a"), run_id="r")
        )
        assert result.decision == decision and result.decided_by == "policy" and result.reason
