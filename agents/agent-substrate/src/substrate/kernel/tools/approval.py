"""HITL (Human-in-the-Loop) approval contract.

When a tool carries ``ToolRisk.HIGH`` or ``ToolRisk.CRITICAL``, the agent
may pause and request human approval before executing.  These types define
the contract between the agent loop and any approval backend (web UI,
Slack bot, CLI prompt, or automated policy engine).

``ApprovalRequest`` is immutable and fully serializable so it can be stored,
forwarded over pub/sub, and resumed after a restart.
``ApprovalDecision`` is the typed response; ``MODIFIED`` carries edited
arguments via ``ApprovalResult.modified_args`` rather than a separate
request/response vocabulary — this is the single approval contract for the
framework (see ``serving/monolith/sse/approval.py::SSEApprovalHandler`` for
the concrete web implementation).
``ApprovalHandler`` is the protocol any backend must implement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol

from substrate.kernel.core.content import JsonObject
from substrate.kernel.core.identity import AgentId
from substrate.kernel.tools import ToolCallRequest, ToolRisk


class ApprovalDecision(StrEnum):
    """Decision returned by an ``ApprovalHandler``."""

    APPROVED = "approved"
    DENIED = "denied"
    SKIPPED = "skipped"
    MODIFIED = "modified"


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


@dataclass(frozen=True, slots=True)
class ApprovalResult:
    """The human's response to an ``ApprovalRequest``.

    ``modified_args`` is only meaningful when ``decision == MODIFIED`` — the
    edited arguments to execute the call with instead of the originally
    requested ones. ``None`` for every other decision.
    """

    decision: ApprovalDecision
    modified_args: JsonObject | None = None


class ApprovalHandler(Protocol):
    """Protocol for approval backends.

    Implementations:
    - ``SSEApprovalHandler`` (``serving/monolith/sse/approval.py``) — routes
      through the web SSE stream, waits for the user's decision.
    - A CLI/Slack/automated-policy handler can implement the same Protocol.

    The agent loop calls ``request()`` synchronously (it awaits it), then
    uses the returned ``ApprovalResult`` to proceed, cancel, or substitute
    modified arguments for the tool call.
    """

    async def request(self, req: ApprovalRequest) -> ApprovalResult:
        """Block until an approval decision is made and return it."""
        ...


__all__ = [
    "ApprovalDecision",
    "ApprovalRequest",
    "ApprovalResult",
    "ApprovalHandler",
]
