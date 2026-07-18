"""SSEApprovalHandler — the web implementation of kernel's ApprovalHandler.

Satisfies ``kernel/tools/approval.py::ApprovalHandler`` directly (structural
typing — no intermediate translation layer). Two paths, mirroring exactly how
``AskHumanTool``/``WebHITLBridge`` split human-input into a durable
signal-based path and a Future-based fallback:

- **Durable (the normal case)**: when the configured ``WebHITLBridge`` has a
  ``signal_bus`` (``suspends_via_signal = True``, set below — same marker
  convention ``WebHITLBridge.__init__`` already uses for
  ``human_handler``), ``ToolInvoker`` never calls ``request()`` on this class
  at all — it suspends the run directly via ``ctx.sleep_until_signal()``
  (``agents/tools/invoker.py``), so a pending approval survives a process
  restart. ``request()`` still exists here for a caller that constructs this
  handler with no signal_bus (tests, or a deliberately non-durable setup) —
  it falls back to the old ``WebHITLBridge.request_and_wait()`` Future.
- ``_approval_result_from_signal`` below has an identical twin in
  ``agents/tools/invoker.py`` (the durable path's own copy) — deliberately
  duplicated, not imported: agents/ (L1) must never import serving/, and
  this ~10-line function is small enough that duplicating it is cheaper
  than the layering violation an import would cost.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from substrate.kernel.tools.approval import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResult,
)

if TYPE_CHECKING:
    from substrate.serving.monolith.sse.bridge import WebHITLBridge


def _approval_result_from_signal(data: dict[str, Any]) -> ApprovalResult:
    """Map the frontend's response payload (``{action, modified_arguments}``,
    or ``{session_disconnected}``/``{timed_out}``) to an ``ApprovalResult``.
    See the module docstring for why this is duplicated, not imported,
    from ``agents/tools/invoker.py``'s identical helper."""
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


class SSEApprovalHandler:
    """Routes a kernel ``ApprovalRequest`` through a ``WebHITLBridge``.

    ``suspends_via_signal`` mirrors ``WebHITLBridge.human_handler``'s own
    marker exactly: True only when the bridge has a real ``signal_bus``
    (durable, cross-replica), so ``ToolInvoker`` can tell whether to suspend
    via signal or fall back to this class's own ``request()``.
    """

    def __init__(self, bridge: "WebHITLBridge") -> None:
        self._bridge = bridge
        self.suspends_via_signal = bridge._signal_bus is not None

    async def request(self, req: ApprovalRequest) -> ApprovalResult:
        """Future-based fallback — used only when this handler was
        constructed against a bridge with no signal_bus. The normal, durable
        path never calls this; see the module docstring."""
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
        return _approval_result_from_signal(data)


__all__ = ["SSEApprovalHandler"]
