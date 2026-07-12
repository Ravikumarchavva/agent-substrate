"""SSEApprovalHandler — the web implementation of kernel's ApprovalHandler.

Satisfies ``kernel/tools/approval.py::ApprovalHandler`` directly (structural
typing — no intermediate translation layer): ``ToolInvoker`` calls
``request(req: ApprovalRequest) -> ApprovalResult`` when a CRITICAL/HIGH-risk
tool call needs a human decision, and this routes it through the same
``WebHITLBridge`` outgoing-queue/Future mechanism ``AskHumanTool`` already
uses for human-input requests — the request appears as a
``tool_approval_request`` SSE event, and the frontend's response (POSTed to
``/chat/respond/{request_id}``, see ``routes/hitl.py``) resolves the Future.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from substrate.kernel.tools.approval import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResult,
)

if TYPE_CHECKING:
    from substrate.serving.monolith.sse.bridge import WebHITLBridge


class SSEApprovalHandler:
    """Routes a kernel ``ApprovalRequest`` through a ``WebHITLBridge``."""

    def __init__(self, bridge: "WebHITLBridge") -> None:
        self._bridge = bridge

    async def request(self, req: ApprovalRequest) -> ApprovalResult:
        request_id = uuid4().hex
        payload = {
            "request_id": request_id,
            "tool_name": req.call.name,
            "call_id": req.call.call_id,
            "arguments": req.call.arguments,
            "context": req.context,
        }
        data = await self._bridge.request_and_wait(
            "tool_approval_request", payload, request_id
        )

        if data.get("session_disconnected") or data.get("timed_out"):
            return ApprovalResult(decision=ApprovalDecision.DENIED)

        action = data.get("action", "deny")
        if action == "modify":
            return ApprovalResult(
                decision=ApprovalDecision.MODIFIED,
                modified_args=data.get("modified_arguments") or {},
            )
        if action == "approve":
            return ApprovalResult(decision=ApprovalDecision.APPROVED)
        return ApprovalResult(decision=ApprovalDecision.DENIED)


__all__ = ["SSEApprovalHandler"]
