"""HITL (Human-in-the-Loop) approval contract.

When a tool carries ``ToolRisk.HIGH`` or ``ToolRisk.CRITICAL``, the agent
may pause and request human approval before executing.  These types define
the contract between the agent loop and any approval backend (web UI,
Slack bot, CLI prompt, or automated policy engine).

``ApprovalRequest`` is immutable and fully serializable so it can be stored,
forwarded over pub/sub, and resumed after a restart.
``ApprovalDecision`` is the typed response.
``ApprovalHandler`` is the protocol any backend must implement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol

from ravi.kernel.core.content import JsonObject
from ravi.kernel.core.identity import AgentId
from ravi.kernel.tools import ToolCallRequest, ToolRisk


class ApprovalDecision(StrEnum):
    """Decision returned by an ``ApprovalHandler``."""

    APPROVED = "approved"
    DENIED = "denied"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """Request for human approval of a pending tool call.

    ``call`` — the tool call awaiting approval.
    ``risk`` — the tool's risk level (why approval is needed).
    ``agent_id`` — which agent is requesting approval.
    ``run_id`` — the execution run; used to resume after approval.
    ``context`` — optional JSON bag for extra metadata (e.g. user message).
    ``requested_at`` — when the request was created (UTC).
    """

    call: ToolCallRequest
    risk: ToolRisk
    agent_id: AgentId
    run_id: str
    context: JsonObject = field(default_factory=dict)
    requested_at: datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )


class ApprovalHandler(Protocol):
    """Protocol for approval backends.

    Implementations:
    - ``WebApprovalHandler``  — sends request to the HITL web service, waits for decision
    - ``AutoApprovalHandler`` — always approves (for testing)
    - ``CliApprovalHandler``  — prompts the terminal operator

    The agent loop calls ``request()`` synchronously (it awaits it), then
    uses the returned decision to proceed or cancel the tool call.
    """

    async def request(self, req: ApprovalRequest) -> ApprovalDecision:
        """Block until an approval decision is made and return it."""
        ...


__all__ = ["ApprovalDecision", "ApprovalRequest", "ApprovalHandler"]
