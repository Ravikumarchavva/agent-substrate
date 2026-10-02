"""Human approval for risky tools.

A tool declares its ``risk``. Anything above what the agent may run on its own pauses the run and waits for a
person — *durably*: the run goes dormant in the store (no thread, no memory held), and whoever decides — hours later,
from another process — wakes it with a signal. The decision is journaled with who made it and why.

    uv run python examples/04_human_approval.py
"""

from __future__ import annotations

import asyncio
from typing import Any

from _model import ToolCall, pick_model

from substrate.agents import ReActAgent
from substrate.runtime import Runtime
from substrate.tools import ApprovalRequest, ApprovalResult, ToolExecutionResult, ToolRisk
from substrate.types import RunLogKind, TextBlock


class WireMoney:
    name = "wire_money"
    description = "Send money to a vendor."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"amount": {"type": "integer"}, "to": {"type": "string"}},
        "required": ["amount", "to"],
    }
    risk = ToolRisk.CRITICAL  # needs a human yes
    idempotent = False  # never re-run if the process dies mid-call: the journal says what was attempted

    async def execute(self, *, ctx: Any = None, amount: int, to: str, **_: Any) -> ToolExecutionResult:
        return ToolExecutionResult(name=self.name, content=[TextBlock(text=f"wired {amount} to {to}")])


class WaitForSignal:
    """An approval handler that suspends the run until a decision arrives as a signal.

    ``suspends_via_signal`` is the whole contract for the durable path: the engine journals the request, puts the
    run to sleep, and wakes it when ``hitl:<request_id>`` is signalled. A UI would signal from its approve button.
    """

    suspends_via_signal = True

    async def request(self, req: ApprovalRequest) -> ApprovalResult:  # pragma: no cover - the durable path is used
        raise NotImplementedError


async def main() -> None:
    agent = ReActAgent(
        "treasurer",
        model=pick_model(ToolCall("wire_money", {"amount": 5000, "to": "ACME"}), "Done: 5000 sent to ACME."),
        tools=[WireMoney()],
        approval_handler=WaitForSignal(),
    )

    async with Runtime.open("./.substrate") as runtime:
        work = asyncio.create_task(runtime.run(agent, "Pay ACME 5000 for invoice 1042.", thread="payments"))

        # The run is now live on its thread. Wait for it to ask.
        run = None
        while run is None:
            await asyncio.sleep(0.02)
            run = await runtime.active_run_for_thread("payments")
        request = None
        async for entry in runtime.tail(run.run_id):
            if entry.kind == RunLogKind.APPROVAL_REQUESTED:
                request = entry.payload
                break
        assert request is not None
        print(f"waiting for a human: {request['tool_name']}({request['args']}) risk={request['risk']}")

        # ...time passes. Someone decides — here, in the same process; it could be any process on the store.
        decision = {"action": "approve", "decided_by": "alice", "reason": "matches invoice 1042"}
        await runtime.store.signal(run.run_id, f"hitl:{request['request_id']}", decision)

        outcome = await work
        print(outcome.output)
        for entry in await runtime.read(run.run_id):
            if entry.kind == RunLogKind.APPROVAL_DECIDED:
                who = {k: entry.payload[k] for k in ("decision", "decided_by", "reason")}
                print("journaled:", who)


if __name__ == "__main__":
    asyncio.run(main())
