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
framework (see ``substrate_cloud/monolith/sse/approval.py::SSEApprovalHandler`` for
the concrete web implementation).
``ApprovalHandler`` is the protocol any backend must implement.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import Field, model_validator

from substrate.types.content import JsonObject, KernelModel
from substrate.types.identity import Actor
from substrate.tools.protocols import ToolCallRequest, ToolRisk


class ApprovalDecision(StrEnum):
    """Decision returned by an ``ApprovalHandler``."""

    APPROVED = "approved"
    DENIED = "denied"
    SKIPPED = "skipped"
    MODIFIED = "modified"


class ApprovalRequest(KernelModel):
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
    agent_id: Actor
    run_id: str
    context: JsonObject = Field(default_factory=dict)
    requested_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )


class ApprovalResult(KernelModel):
    """The human's response to an ``ApprovalRequest``.

    ``modified_args`` is only meaningful when ``decision == MODIFIED`` — the
    edited arguments to execute the call with instead of the originally
    requested ones. ``None`` for every other decision.

    ``decided_by`` / ``decided_at`` / ``reason`` make the decision attributable: who made
    it, when, and why. The serving layer stamps the first two from the authenticated caller —
    never from anything the client sent — and the invoker journals all three, because an
    approval nobody can be held to is not a control.
    """

    decision: ApprovalDecision
    modified_args: JsonObject | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    reason: str | None = None

    @classmethod
    def from_response(cls, data: Mapping[str, Any]) -> "ApprovalResult":
        """Read the response a front end (or a timeout) delivered. ``{action: approve|modify|deny}``,
        plus the stamped ``decided_by`` / ``decided_at``; a disconnect or timeout is a denial."""
        stamp: dict[str, Any] = {
            "decided_by": data.get("decided_by"),
            "reason": data.get("reason") or None,
        }
        decided_at = data.get("decided_at")
        if isinstance(decided_at, str):
            decided_at = datetime.fromisoformat(decided_at)
        stamp["decided_at"] = decided_at if isinstance(decided_at, datetime) else None
        if data.get("session_disconnected") or data.get("timed_out"):
            return cls(decision=ApprovalDecision.DENIED, **stamp)
        action = data.get("action", "deny")
        if action == "modify":
            return cls(
                decision=ApprovalDecision.MODIFIED,
                modified_args=data.get("modified_arguments") or {},
                **stamp,
            )
        if action == "approve":
            return cls(decision=ApprovalDecision.APPROVED, **stamp)
        return cls(decision=ApprovalDecision.DENIED, **stamp)

    @model_validator(mode="after")
    def _args_only_when_modified(self) -> "ApprovalResult":
        if self.decision == ApprovalDecision.MODIFIED and self.modified_args is None:
            raise ValueError("a MODIFIED decision needs `modified_args`")
        if (
            self.decision != ApprovalDecision.MODIFIED
            and self.modified_args is not None
        ):
            raise ValueError(
                "`modified_args` is only meaningful for a MODIFIED decision"
            )
        return self


@runtime_checkable
class ApprovalHandler(Protocol):
    """Protocol for approval backends.

    Implementations:
    - ``SSEApprovalHandler`` (``substrate_cloud/monolith/sse/approval.py``) — routes
      through the web SSE stream, waits for the user's decision.
    - A CLI/Slack/automated-policy handler can implement the same Protocol.

    The agent loop calls ``request()`` synchronously (it awaits it), then
    uses the returned ``ApprovalResult`` to proceed, cancel, or substitute
    modified arguments for the tool call.
    """

    async def request(self, req: ApprovalRequest) -> ApprovalResult:
        """Block until an approval decision is made and return it."""
        ...


def approval_signal(request_id: str) -> str:
    """The name of the signal that carries a decision for ``request_id`` to the run waiting on it."""
    return f"hitl:{request_id}"


class DurableApproval:
    """The approval handler for a human who may take hours, or live in another process.

    The engine journals the request, puts the run to sleep — no thread or memory held, and it survives a restart — and
    resumes it when the decision arrives as a signal: ``await runtime.decide(run_id, request_id, ...)`` from any process
    on the same store. ``runtime.pending_approvals(run_id)`` lists what is waiting.
    """

    suspends_via_signal = True

    async def request(
        self, req: ApprovalRequest
    ) -> ApprovalResult:  # pragma: no cover - the durable path is taken
        raise RuntimeError(
            "DurableApproval waits for a signal (runtime.decide); it is never asked directly"
        )


class AutoApprove:
    """A policy, not a person: approve every call whose risk is at most ``up_to``, deny anything above.

    For scripts, tests and unattended agents that should be able to do some risky things and never the worst.
    """

    def __init__(self, up_to: ToolRisk = ToolRisk.HIGH, *, by: str = "policy") -> None:
        self._up_to = up_to
        self._by = by

    async def request(self, req: ApprovalRequest) -> ApprovalResult:
        allowed = req.risk <= self._up_to
        return ApprovalResult(
            decision=ApprovalDecision.APPROVED if allowed else ApprovalDecision.DENIED,
            decided_by=self._by,
            decided_at=datetime.now(tz=timezone.utc),
            reason=f"risk {req.risk.value} is {'within' if allowed else 'above'} {self._up_to.value}",
        )


__all__ = [
    "ApprovalDecision",
    "ApprovalRequest",
    "ApprovalResult",
    "ApprovalHandler",
    "AutoApprove",
    "DurableApproval",
    "approval_signal",
]
