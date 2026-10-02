"""Human approval for risky tools.

A tool declares its ``risk``. Anything above what the agent may run on its own pauses the run and waits for a
person — *durably*: the run goes dormant in the store (no thread, no memory held), and whoever decides — hours later,
from another process — answers with ``runtime.decide``. The decision is journaled with who made it and why.

    uv run python examples/04_human_approval.py
"""

from __future__ import annotations

import asyncio

from _model import ToolCall, pick_model

from substrate.agents import ReActAgent
from substrate.runtime import Runtime
from substrate.tools import ApprovalDecision, DurableApproval, ToolRisk, tool
from substrate.types import RunLogKind


@tool(risk=ToolRisk.CRITICAL, idempotent=False)  # CRITICAL needs a human yes; never re-run if the process dies mid-call
def wire_money(amount: int, to: str) -> str:
    """Send money to a vendor."""
    return f"wired {amount} to {to}"


async def main() -> None:
    agent = ReActAgent(
        "treasurer",
        model=pick_model(ToolCall("wire_money", {"amount": 5000, "to": "ACME"}), "Done: 5000 sent to ACME."),
        tools=[wire_money],
        approval_handler=DurableApproval(),
    )

    async with Runtime.open("./.substrate") as runtime:
        work = asyncio.create_task(runtime.run(agent, "Pay ACME 5000 for invoice 1042.", thread="payments"))

        # The run is now live on its thread. Wait for it to ask.
        run = None
        while run is None or not (pending := await runtime.pending_approvals(run.run_id)):
            await asyncio.sleep(0.02)
            run = await runtime.active_run_for_thread("payments")
        request = pending[0]
        print(f"waiting for a human: {request.tool_name}({request.args}) risk={request.risk}")

        # ...time passes. Someone decides — here, in the same process; it could be any process on the store.
        await runtime.decide(
            run.run_id, request.request_id, ApprovalDecision.APPROVED, by="alice", reason="matches invoice 1042"
        )

        outcome = await work
        print(outcome.output)
        for entry in await runtime.read(run.run_id):
            if entry.kind == RunLogKind.APPROVAL_DECIDED:
                who = {k: entry.payload[k] for k in ("decision", "decided_by", "reason")}
                print("journaled:", who)


if __name__ == "__main__":
    asyncio.run(main())
