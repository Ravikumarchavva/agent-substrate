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
