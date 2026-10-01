"""SSEApprovalHandler — the web implementation of kernel's ApprovalHandler.

Satisfies ``kernel/tools/approval.py::ApprovalHandler`` directly (structural
typing — no intermediate translation layer). Two paths, mirroring exactly how
``AskHumanTool``/``WebHITLBridge`` split human-input into a durable
signal-based path and a Future-based fallback:

- **Durable (the normal case)**: when the configured ``WebHITLBridge`` has a
  the runtime store (``suspends_via_signal = True``, set below — same marker
  convention ``WebHITLBridge.__init__`` already uses for
  ``human_handler``), ``ToolInvoker`` never calls ``request()`` on this class
  at all — it suspends the run directly via ``ctx.sleep_until_signal()``
  (``agents/tools/invoker.py``), so a pending approval survives a process
  restart. ``request()`` still exists here for a caller that constructs this
  handler with no runtime store (tests, or a deliberately non-durable setup) —
  it falls back to the old ``WebHITLBridge.request_and_wait()`` Future.
- The response is read by ``ApprovalResult.from_response`` — the one mapping, shared with
  the durable path in ``ToolInvoker``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from substrate.kernel.abstractions.tools.approval import (
    ApprovalRequest,
    ApprovalResult,
)

if TYPE_CHECKING:
    from substrate.serving.monolith.sse.bridge import WebHITLBridge


class SSEApprovalHandler:
    """Routes a kernel ``ApprovalRequest`` through a ``WebHITLBridge``.

    ``suspends_via_signal`` mirrors ``WebHITLBridge.human_handler``'s own
    marker exactly: True only when the bridge has a real runtime store
    (durable, cross-replica), so ``ToolInvoker`` can tell whether to suspend
    via signal or fall back to this class's own ``request()``.
    """

    def __init__(self, bridge: "WebHITLBridge") -> None:
        self._bridge = bridge
        self.suspends_via_signal = bridge._store is not None

    async def request(self, req: ApprovalRequest) -> ApprovalResult:
        """Future-based fallback — used only when this handler was
        constructed against a bridge with no runtime store. The normal, durable
        path never calls this; see the module docstring."""
        request_id = uuid4().hex
        payload = {
            "request_id": request_id,
            "tool_name": req.call.name,
            "call_id": req.call.call_id,
            "arguments": req.call.arguments,
            "context": req.context,
            "risk": req.risk.value,
            "summary": req.context.get("summary", ""),
        }
        data = await self._bridge.request_and_wait(
            "tool_approval_request", payload, request_id
        )
        return ApprovalResult.from_response(data)


__all__ = ["SSEApprovalHandler"]
