"""Invariant register — an approval can be held to someone (row I23).

A human's yes is what lets a high-risk tool run, so a decision nobody can attribute is not a
control. Each decision is journaled with who made it, when and why, and *who* and *when* are what
the server saw — a client cannot name the approver in its own request.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from substrate.kernel.abstractions.core.content import TextBlock
from substrate.kernel.abstractions.core.identity import Actor
from substrate.kernel.abstractions.messaging.message import DataPayload, Message
from substrate.kernel.abstractions.runtime.log_entry import RunLogKind
from substrate.kernel.abstractions.tools import ToolExecutionResult, ToolRisk
from substrate.kernel.abstractions.tools.approval import ApprovalDecision, ApprovalRequest, ApprovalResult
from substrate.kernel.runtime import Runtime
from substrate.kernel.tools.toolbox import Toolbox


class WireMoney:
    name = "wire_money"
    description = "Wires money."
    input_schema: dict[str, Any] = {"type": "object", "properties": {"amount": {"type": "integer"}}}
    risk = ToolRisk.CRITICAL
    idempotent = False

    async def execute(self, *, ctx: Any = None, **kwargs: Any) -> ToolExecutionResult:
        return ToolExecutionResult(name=self.name, content=[TextBlock(text=f"wired {kwargs.get('amount')}")])


class SignalApproval:
    suspends_via_signal = True

    async def request(self, req: ApprovalRequest) -> ApprovalResult:  # pragma: no cover - the durable path is used
        raise AssertionError("the durable path must be used")


class Treasurer:
    def __init__(self) -> None:
        self.id = Actor("agent", "treasurer")
        self.tools = Toolbox()
        self.tools.add(WireMoney())
        self.approval_handler = SignalApproval()
        self.results: list[Any] = []

    async def run(self, ctx: Any, inbox: list[Message]) -> None:
        self.results.append(await ctx.tool("wire_money", {"amount": 5000}))


async def _decide(path: Path, response: dict[str, Any]) -> tuple[Treasurer, list[dict[str, Any]]]:
    agent = Treasurer()
    async with Runtime.local(path) as rt:
        await rt.register(agent)
        run_id = await rt.submit(agent.id, Message(target=agent.id, sender=Actor.system("t"), payload=DataPayload(data={})))

        async def requested() -> dict[str, Any]:
            async for entry in rt.tail(run_id):
                if entry.kind == RunLogKind.APPROVAL_REQUESTED:
                    return dict(entry.payload or {})
            raise AssertionError("no approval was requested")

        request = await asyncio.wait_for(requested(), 10)
        await rt.store.signal(run_id, f"hitl:{request['request_id']}", response)

        async def finished() -> None:
            async for entry in rt.tail(run_id):
                if entry.kind in (RunLogKind.RUN_COMPLETED, RunLogKind.RUN_FAILED):
                    return

        await asyncio.wait_for(finished(), 10)
        decided = [dict(e.payload) for e in await rt.read(run_id) if e.kind == RunLogKind.APPROVAL_DECIDED]
    return agent, decided


async def test_i23_an_approval_is_journaled_with_who_when_and_why(tmp_path: Path) -> None:
    when = datetime(2030, 5, 1, 12, 0, tzinfo=timezone.utc).isoformat()
    agent, decided = await _decide(
        tmp_path / "rt.sqlite3",
        {"action": "approve", "decided_by": "user-42", "decided_at": when, "reason": "matches the invoice"},
    )

    (entry,) = decided
    assert entry["decision"] == "approved" and entry["tool_name"] == "wire_money" and entry["risk"] == "critical"
    assert entry["decided_by"] == "user-42" and entry["decided_at"] == when and entry["reason"] == "matches the invoice"
    assert agent.results[0].status == "ok"


async def test_i23_a_denial_is_journaled_too(tmp_path: Path) -> None:
    agent, decided = await _decide(tmp_path / "rt.sqlite3", {"action": "deny", "decided_by": "user-7", "reason": "wrong account"})

    (entry,) = decided
    assert entry["decision"] == "denied" and entry["decided_by"] == "user-7" and entry["reason"] == "wrong account"
    assert agent.results[0].status == "denied"


def test_the_result_carries_the_attribution_it_was_given() -> None:
    result = ApprovalResult.from_response({"action": "approve", "decided_by": "u", "decided_at": "2030-01-01T00:00:00+00:00", "reason": "ok"})
    assert result.decision is ApprovalDecision.APPROVED and result.decided_by == "u" and result.reason == "ok"
    assert result.decided_at == datetime(2030, 1, 1, tzinfo=timezone.utc)


def test_a_disconnect_or_timeout_is_a_denial() -> None:
    assert ApprovalResult.from_response({"timed_out": True}).decision is ApprovalDecision.DENIED
    assert ApprovalResult.from_response({"session_disconnected": True}).decision is ApprovalDecision.DENIED


async def test_i23_the_server_names_the_approver_not_the_client() -> None:
    """The route stamps ``decided_by`` / ``decided_at`` from the authenticated caller. A client that
    puts someone else's name in its body is not believed."""
    from substrate.serving.monolith.routes.hitl import respond_to_hitl
    from substrate.serving.monolith.schemas import HITLResponse

    received: dict[str, Any] = {}

    class Registry:
        async def resolve(self, request_id: str, data: dict[str, Any]) -> bool:
            received.update(data)
            return True

    ctx = SimpleNamespace(bridge_registry=Registry())
    claims = SimpleNamespace(sub="the-real-caller")
    body = HITLResponse.model_validate({"action": "approve", "decided_by": "the-cfo", "reason": "ok"})

    await respond_to_hitl("req-1", body, ctx=ctx, user=claims)  # type: ignore[arg-type]

    assert received["decided_by"] == "the-real-caller"
    assert received["decided_at"], "the time of the decision is stamped by the server"
    assert received["action"] == "approve" and received["reason"] == "ok"
