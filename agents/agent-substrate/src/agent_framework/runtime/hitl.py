"""WebHITL Bridge — connects the agent's blocking HITL requests to HTTP/SSE.

The bridge is the glue between:
  - The agent (which blocks on ``await handler.request_input()`` or
    ``await handler.request_approval()``)
  - The HTTP layer (which streams SSE events to the frontend and
    receives responses via a separate POST endpoint)

Flow:
  1. Agent calls a tool → approval handler fires →
     ``bridge.request_and_wait()`` puts an event on the outgoing queue
     and creates an ``asyncio.Future``.
  2. The SSE generator drains the outgoing queue and sends the event
     to the frontend.
  3. The frontend shows a UI card (ToolApprovalCard or HumanInputCard)
     and POSTs the user's response to ``/chat/respond/{request_id}``.
  4. The POST endpoint calls ``bridge.resolve(request_id, data)`` which
     completes the Future.
  5. The agent resumes with the response.

Usage::

    bridge = WebHITLBridge()
    agent = ReActAgent(
        ...,
        tool_approval_handler=bridge.approval_handler,
        tools=[AskHumanTool(handler=bridge.human_handler), ...],
    )
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from agent_framework.extensions.tools.human_input import (
    CallbackApprovalHandler,
    CallbackHumanHandler,
    HumanInputRequest,
    HumanInputResponse,
    ToolApprovalAction,
    ToolApprovalHandler,
    ToolApprovalRequest,
    ToolApprovalResponse,
    HumanInputHandler,
)

logger = logging.getLogger("agent_framework.web_hitl")

# Sentinel used to signal the SSE generator that the agent is done
_DONE = object()

# Public alias — consumers can import BRIDGE_DONE instead of the private _DONE.
BRIDGE_DONE = _DONE


class WebHITLBridge:
    """Bidirectional bridge between the agent's HITL handlers and HTTP/SSE.

    Outgoing (agent → frontend):
        Events are placed on ``_outgoing`` queue.  The SSE generator
        calls ``get_event()`` to drain them.

    Incoming (frontend → agent):
        ``resolve(request_id, data)`` completes the matching Future.

    Both ``approval_handler`` and ``human_handler`` are pre-built
    ``CallbackApprovalHandler`` / ``CallbackHumanHandler`` instances
    that route through this bridge.

    Lock-free HITL:
        When a browser disconnects while a HITL Future is pending, the
        bridge stays alive in the ``BridgeRegistry``.  The agent task keeps
        running (blocked on the Future).  The user can reconnect and respond
        via ``POST /chat/respond/{request_id}``.  Use ``has_pending`` to
        check before deciding whether to release the bridge.
    """

    def __init__(self, response_timeout: float = 300.0):
        self._outgoing: asyncio.Queue[Any] = asyncio.Queue()
        self._pending: Dict[str, asyncio.Future[Dict[str, Any]]] = {}
        self._pending_payloads: Dict[str, Dict[str, Any]] = {}
        self._response_timeout = response_timeout

        # Pre-built handlers wired to this bridge
        self._approval_handler = CallbackApprovalHandler(
            callback=self._handle_approval,
        )
        self._human_handler = CallbackHumanHandler(
            callback=self._handle_human_input,
        )

    # -- Public properties ---------------------------------------------------

    @property
    def approval_handler(self) -> ToolApprovalHandler:
        """ToolApprovalHandler to pass to the agent."""
        return self._approval_handler

    @property
    def human_handler(self) -> HumanInputHandler:
        """HumanInputHandler to pass to AskHumanTool."""
        return self._human_handler

    # -- Pending state introspection ─────────────────────────────────────

    @property
    def has_pending(self) -> bool:
        """True when at least one HITL request is awaiting user response."""
        return bool(self._pending)

    def get_pending_info(self) -> list[dict[str, Any]]:
        """Return metadata about all pending HITL requests.

        Used by the ``GET /hitl/status/{thread_id}`` endpoint so the frontend
        can restore approval/input cards after a reconnect.
        """
        # We store the sent event payloads alongside Futures so we can
        # replay them.  Fall back to just the request_id if no payload saved.
        return [
            {
                "request_id": rid,
                **(self._pending_payloads.get(rid) or {}),
            }
            for rid in self._pending
        ]

    # -- Disconnect / cancellation -----------------------------------------------

    def cancel_all_pending(self, reason: str = "session_disconnected") -> int:
        """Resolve all pending HITL futures immediately with a disconnect signal.

        Called when the client browser disconnects so the blocked agent can
        resume (and likely abort), rather than waiting for the full timeout.

        Args:
            reason: Short machine-readable reason string stored under the
                    ``"reason"`` key in the resolved dict.  Defaults to
                    ``"session_disconnected"``.

        Returns:
            Number of futures that were resolved.
        """
        resolved = 0
        for request_id, future in list(self._pending.items()):
            if not future.done():
                future.set_result({"session_disconnected": True, "reason": reason})
                resolved += 1
        self._pending.clear()
        self._pending_payloads.clear()
        if resolved:
            logger.info(
                "WebHITLBridge: cancelled %d pending HITL request(s) (%s)",
                resolved,
                reason,
            )
        return resolved

    # -- Outgoing queue (agent → SSE → frontend) ----------------------------

    async def get_event(self) -> Any:
        """Get next event for the SSE stream. Returns _DONE sentinel when finished."""
        return await self._outgoing.get()

    async def put_event(self, event: Dict[str, Any]) -> None:
        """Put an event onto the outgoing queue (used by the SSE merger)."""
        await self._outgoing.put(event)

    async def signal_done(self) -> None:
        """Signal that the agent has finished (no more events)."""
        await self._outgoing.put(_DONE)

    # -- Incoming resolution (frontend → POST → agent) ----------------------

    def resolve(self, request_id: str, data: Dict[str, Any]) -> bool:
        """Resolve a pending HITL request with the user's response.

        Returns True if the request was found and resolved, False otherwise.
        """
        future = self._pending.pop(request_id, None)
        self._pending_payloads.pop(request_id, None)
        if future is None:
            logger.warning(f"No pending HITL request for id={request_id}")
            return False
        if future.done():
            logger.warning(f"HITL request {request_id} already resolved")
            return False
        future.set_result(data)
        logger.info(f"Resolved HITL request {request_id}")
        return True

    # -- Internal: request-and-wait pattern ----------------------------------

    async def _request_and_wait(
        self,
        event_type: str,
        payload: Dict[str, Any],
        request_id: str,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Put an event on the outgoing queue and wait for the response."""
        effective_timeout = timeout if timeout is not None else self._response_timeout
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future

        # Save the full event payload so we can replay it on frontend reconnect
        self._pending_payloads[request_id] = {
            "type": event_type,
            **payload,
        }

        # Send event to frontend via SSE
        await self._outgoing.put(
            {
                "type": event_type,
                **payload,
            }
        )

        logger.info(
            f"HITL {event_type} sent (id={request_id}), "
            f"waiting up to {effective_timeout}s"
        )

        try:
            result = await asyncio.wait_for(future, timeout=effective_timeout)
            return result
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            self._pending_payloads.pop(request_id, None)
            logger.warning(f"HITL request {request_id} timed out")
            return {"timed_out": True}

    # -- Callback: tool approval ---------------------------------------------

    async def _handle_approval(
        self, request: ToolApprovalRequest
    ) -> ToolApprovalResponse:
        """Called when the agent needs tool approval — routes through SSE.

        Behaviour is driven by ``request.hitl_mode``:

        FIRE_AND_CONTINUE
            Puts the event on the outgoing SSE queue and immediately returns
            APPROVE.  The agent does not wait for the user at all.

        CONTINUE_ON_TIMEOUT
            Waits up to ``request.hitl_timeout_seconds`` (default 30s).
            If the user responds in time their decision is applied;
            on timeout the tool is auto-approved with original arguments.

        BLOCKING  (default)
            Waits up to the bridge-wide ``response_timeout``.  On timeout
            the tool is DENIED — missing a response is treated as a veto.
        """
        payload = {
            "request_id": request.request_id,
            "tool_name": request.tool_name,
            "call_id": request.call_id,
            "arguments": request.arguments,
            "context": request.context,
            "hitl_mode": request.hitl_mode,
        }

        # ── FIRE_AND_CONTINUE: publish event, never wait ───────────────────
        if request.hitl_mode == "fire_and_continue":
            await self._outgoing.put({"type": "tool_approval_request", **payload})
            logger.info(
                "HITL fire_and_continue: sent event for %s, not waiting",
                request.tool_name,
            )
            return ToolApprovalResponse(
                request_id=request.request_id,
                action=ToolApprovalAction.APPROVE,
                reason="Auto-approved (fire_and_continue mode)",
            )

        # ── CONTINUE_ON_TIMEOUT / BLOCKING: send event and wait ───────────
        timeout = (
            request.hitl_timeout_seconds or 30.0
            if request.hitl_mode == "continue_on_timeout"
            else self._response_timeout
        )
        data = await self._request_and_wait(
            "tool_approval_request", payload, request.request_id, timeout=timeout
        )

        if data.get("session_disconnected"):
            return ToolApprovalResponse(
                request_id=request.request_id,
                action=ToolApprovalAction.DENY,
                reason="Session disconnected — tool denied",
            )

        if data.get("timed_out"):
            if request.hitl_mode == "continue_on_timeout":
                logger.info(
                    "HITL continue_on_timeout: timed out for %s, auto-approving",
                    request.tool_name,
                )
                return ToolApprovalResponse(
                    request_id=request.request_id,
                    action=ToolApprovalAction.APPROVE,
                    reason="Auto-approved after timeout (continue_on_timeout mode)",
                )
            # BLOCKING mode: timeout → deny
            return ToolApprovalResponse(
                request_id=request.request_id,
                action=ToolApprovalAction.DENY,
                reason="Approval timed out — denied by default",
            )

        action_str = data.get("action", "deny")
        try:
            action = ToolApprovalAction(action_str)
        except ValueError:
            action = ToolApprovalAction.DENY

        return ToolApprovalResponse(
            request_id=request.request_id,
            action=action,
            modified_arguments=data.get("modified_arguments"),
            reason=data.get("reason", ""),
        )

    # -- Callback: human input -----------------------------------------------

    async def _handle_human_input(
        self, request: HumanInputRequest
    ) -> HumanInputResponse:
        """Called when AskHumanTool fires — routes through SSE."""
        payload = {
            "request_id": request.request_id,
            "question": request.question,
            "context": request.context,
            "options": [
                {"key": o.key, "label": o.label, "description": o.description}
                for o in request.options
            ],
            "allow_freeform": request.allow_freeform,
        }

        data = await self._request_and_wait(
            "human_input_request", payload, request.request_id
        )

        if data.get("session_disconnected"):
            return HumanInputResponse(
                request_id=request.request_id,
                timed_out=True,
                freeform_text="[session disconnected]",
            )

        if data.get("timed_out"):
            return HumanInputResponse(
                request_id=request.request_id,
                timed_out=True,
            )

        return HumanInputResponse(
            request_id=request.request_id,
            selected_key=data.get("selected_key"),
            selected_label=data.get("selected_label", ""),
            freeform_text=data.get("freeform_text"),
        )


# ---------------------------------------------------------------------------
# BridgeRegistry — per-thread bridge pool
# ---------------------------------------------------------------------------


class BridgeRegistry:
    """Manages one WebHITLBridge per active thread (conversation).

    Bridges are created lazily when a chat SSE stream starts and destroyed
    when the stream ends **unless** a HITL request is still pending.  In
    that case the bridge stays alive so the user can reconnect and respond
    without losing the agent's blocked context.

    Resolution uses UUID uniqueness to scan bridges without a secondary
    request_id → thread_id index (UUIDs are collision-free in practice).
    """

    def __init__(self, response_timeout: float = 300.0) -> None:
        self._timeout = response_timeout
        self._bridges: Dict[str, "WebHITLBridge"] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, thread_id: str) -> "WebHITLBridge":
        """Return the live bridge for *thread_id*, creating one if needed."""
        async with self._lock:
            if thread_id not in self._bridges:
                self._bridges[thread_id] = WebHITLBridge(self._timeout)
                logger.debug("BridgeRegistry: created bridge for thread %s", thread_id)
            return self._bridges[thread_id]

    async def release(self, thread_id: str) -> None:
        """Unconditionally destroy the bridge for *thread_id*.

        Prefer ``release_if_idle`` in the SSE generator's ``finally`` block
        so bridges with pending HITL requests survive browser disconnects.
        """
        async with self._lock:
            self._bridges.pop(thread_id, None)
            logger.debug("BridgeRegistry: released bridge for thread %s", thread_id)

    async def release_if_idle(self, thread_id: str) -> bool:
        """Release the bridge only if it has **no** pending HITL requests.

        Returns True if the bridge was released, False if it was kept alive
        because a HITL request is still pending (user can still respond).
        """
        async with self._lock:
            bridge = self._bridges.get(thread_id)
            if bridge is None:
                return True
            if bridge.has_pending:
                logger.info(
                    "BridgeRegistry: keeping bridge alive for thread %s "
                    "— %d pending HITL request(s)",
                    thread_id,
                    len(bridge._pending),
                )
                return False
            self._bridges.pop(thread_id, None)
            logger.debug(
                "BridgeRegistry: released idle bridge for thread %s", thread_id
            )
            return True

    def resolve(self, request_id: str, data: Dict[str, Any]) -> bool:
        """Find the bridge that owns *request_id* and resolve it.

        Scans all active bridges.  Because request IDs are UUIDs, collisions
        are statistically impossible across bridges.
        """
        for bridge in list(self._bridges.values()):
            if request_id in bridge._pending:
                return bridge.resolve(request_id, data)
        logger.warning("BridgeRegistry: no pending request for id=%s", request_id)
        return False

    def get(self, thread_id: str) -> Optional["WebHITLBridge"]:
        """Return the bridge for *thread_id* if active, else None."""
        return self._bridges.get(thread_id)

    def get_pending_hitl(self, thread_id: str) -> list[dict[str, Any]]:
        """Return pending HITL request info for *thread_id*.

        Used by the ``GET /hitl/status/{thread_id}`` endpoint.
        """
        bridge = self._bridges.get(thread_id)
        if bridge is None:
            return []
        return bridge.get_pending_info()

    async def emit(self, thread_id: str, event: Dict[str, Any]) -> None:
        """Emit an event to the active bridge for *thread_id* (no-op if gone)."""
        bridge = self._bridges.get(thread_id)
        if bridge:
            await bridge.put_event(event)
