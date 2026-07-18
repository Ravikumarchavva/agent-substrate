"""Tool-approval HITL is durable: a pending CRITICAL/HIGH-risk approval
survives a process restart, the same way ask_human already does — proven
here by resuming from a *fresh* RunContext folded from the same EventLogProtocol,
exactly simulating two independent process lifetimes sharing only the
durable EventLogProtocol + SignalBusProtocol (see test_effect_cache.py's identical pattern
for the "crash and replay" fixture shape)."""

from __future__ import annotations

from typing import Any

import pytest

from substrate.agents.runtime.backends._event_log import InMemoryEventLog
from substrate.agents.runtime.backends._fanout import PushAllFanout
from substrate.agents.runtime.backends._follow_graph import InMemoryFollowGraph
from substrate.agents.runtime.backends._inbox import InMemoryInbox
from substrate.agents.runtime.backends._scheduler import InMemoryScheduler
from substrate.agents.runtime.backends._signal_bus import InMemorySignalBus
from substrate.agents.runtime.backends._supervisor import InMemorySupervisor
from substrate.agents.runtime.cancellation import CancellationToken
from substrate.agents.runtime.context import RunContext
from substrate.agents.runtime.effect_cache import EffectCache
from substrate.agents.tools.invoker import ToolInvoker
from substrate.agents.tools.toolbox import Toolbox
from substrate.kernel.agent.runtime_context import RunMeta
from substrate.kernel.core.content import TextBlock
from substrate.kernel.core.errors import SuspendInterrupt
from substrate.kernel.runtime.ids import new_run_id
from substrate.kernel.tools import ToolExecutionResult, ToolRisk
from substrate.kernel.tools.approval import ApprovalRequest, ApprovalResult


class SendEmailTool:
    name = "send_email"
    description = "Sends an email."
    risk = ToolRisk.HIGH
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"to": {"type": "string"}},
    }

    async def execute(self, *, ctx: Any = None, **kwargs: Any) -> ToolExecutionResult:
        return ToolExecutionResult(
            content=[TextBlock(text=f"email sent to {kwargs.get('to')}")]
        )


class FakeSignalApprovalHandler:
    """Marks itself signal-capable, matching SSEApprovalHandler's own
    marker convention. request() must never be called by ToolInvoker when
    this marker is set and ctx is available — if it is, the fallback path
    fired instead of the durable one, which is the bug this test guards
    against."""

    suspends_via_signal = True

    async def request(self, req: ApprovalRequest) -> ApprovalResult:
        raise AssertionError(
            "request() called — ToolInvoker should have used the signal "
            "path (suspends_via_signal=True + ctx provided), not the "
            "Future-based fallback"
        )


async def _fresh_ctx(event_log: InMemoryEventLog, run_id: str) -> RunContext:
    """Build a standalone RunContext for run_id, folding fresh from
    event_log — a new instance each call simulates a new process picking up
    the same durable state, not the same in-memory object resuming."""
    inbox = InMemoryInbox()
    scheduler = InMemoryScheduler()
    signal_bus = InMemorySignalBus(scheduler)
    registry = Toolbox()
    registry.add(SendEmailTool())
    tool_invoker = ToolInvoker(
        registry=registry, approval_handler=FakeSignalApprovalHandler()
    )
    meta = RunMeta(run_id=run_id, cancellation=CancellationToken())
    effect_cache = await EffectCache.fold(event_log, run_id)
    return RunContext(
        meta=meta,
        event_log=event_log,
        effect_cache=effect_cache,
        inbox=inbox,
        follow_graph=InMemoryFollowGraph(),
        fanout=PushAllFanout(),
        scheduler=scheduler,
        supervisor=InMemorySupervisor(event_log, inbox, scheduler, signal_bus),
        signal_bus=signal_bus,
        tool_invoker=tool_invoker,
    )


def _extract_request_id(entries: list) -> str:
    for entry in entries:
        if entry.kind == "approval.requested":
            return entry.payload["request_id"]
    raise AssertionError("no approval.requested entry found in the EventLogProtocol")


async def test_tool_approval_survives_restart_and_resumes_when_approved():
    event_log = InMemoryEventLog()
    run_id = new_run_id()

    # "Process 1": the run reaches the HIGH-risk tool call, suspends —
    # nothing resolves the approval yet.
    ctx1 = await _fresh_ctx(event_log, run_id)
    with pytest.raises(SuspendInterrupt):
        await ctx1.tool("send_email", to="user@example.com")

    entries = [e async for e in event_log.read(run_id)]
    request_id = _extract_request_id(entries)

    # The human responds while nothing is running — exactly what a process
    # restart between suspend and response looks like. In-memory backends
    # have no shared external process to signal through, so this test
    # shares ctx1's SignalBusProtocol instance with ctx2 below as the stand-in for
    # what a real SignalBus already gives for free (any process can
    # signal the same durable run) — see the two Postgres-backed
    # Supervisor tests for the equivalent proof against the real
    # backend.
    await ctx1._signal_bus.signal(  # type: ignore[attr-defined]
        run_id, f"hitl:{request_id}", {"action": "approve"}
    )

    # "Process 2": a brand-new RunContext, folded fresh from the EventLogProtocol —
    # no shared Python object with ctx1 except event_log itself.
    ctx2 = await _fresh_ctx(event_log, run_id)
    ctx2._signal_bus = ctx1._signal_bus  # type: ignore[attr-defined]
    result = await ctx2.tool("send_email", to="user@example.com")

    assert result.status == "ok"
    assert result.text is not None
    assert "user@example.com" in result.text


async def test_tool_approval_survives_restart_and_denies_when_rejected():
    event_log = InMemoryEventLog()
    run_id = new_run_id()

    ctx1 = await _fresh_ctx(event_log, run_id)
    with pytest.raises(SuspendInterrupt):
        await ctx1.tool("send_email", to="user@example.com")

    entries = [e async for e in event_log.read(run_id)]
    request_id = _extract_request_id(entries)

    await ctx1._signal_bus.signal(  # type: ignore[attr-defined]
        run_id, f"hitl:{request_id}", {"action": "deny"}
    )

    ctx2 = await _fresh_ctx(event_log, run_id)
    ctx2._signal_bus = ctx1._signal_bus  # type: ignore[attr-defined]
    result = await ctx2.tool("send_email", to="user@example.com")

    assert result.status == "denied"


async def test_tool_approval_request_id_is_replay_stable():
    """A second suspend attempt on the SAME run (no signal yet) must reuse
    the identical request_id — otherwise every retry would orphan the
    previous SSE card, exactly the bug ctx.uuid() (not uuid4()) prevents."""
    event_log = InMemoryEventLog()
    run_id = new_run_id()

    ctx1 = await _fresh_ctx(event_log, run_id)
    with pytest.raises(SuspendInterrupt):
        await ctx1.tool("send_email", to="user@example.com")
    first_entries = [e async for e in event_log.read(run_id)]
    first_id = _extract_request_id(first_entries)

    # Replay without ever resolving the signal: must re-suspend, and the
    # approval.requested entry must NOT be duplicated (log_once).
    ctx2 = await _fresh_ctx(event_log, run_id)
    ctx2._signal_bus = ctx1._signal_bus  # type: ignore[attr-defined]
    with pytest.raises(SuspendInterrupt):
        await ctx2.tool("send_email", to="user@example.com")

    second_entries = [e async for e in event_log.read(run_id)]
    approval_entries = [e for e in second_entries if e.kind == "approval.requested"]
    assert len(approval_entries) == 1
    assert approval_entries[0].payload["request_id"] == first_id
