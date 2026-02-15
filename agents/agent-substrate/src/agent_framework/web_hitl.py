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

from agent_framework.human_input import (
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
    """

    def __init__(self, response_timeout: float = 300.0):
        self._outgoing: asyncio.Queue[Any] = asyncio.Queue()
        self._pending: Dict[str, asyncio.Future[Dict[str, Any]]] = {}
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
        self, event_type: str, payload: Dict[str, Any], request_id: str
    ) -> Dict[str, Any]:
        """Put an event on the outgoing queue and wait for the response."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future

        # Send event to frontend via SSE
        await self._outgoing.put({
            "type": event_type,
            **payload,
        })

        logger.info(
            f"HITL {event_type} sent (id={request_id}), "
            f"waiting up to {self._response_timeout}s"
        )

        try:
            result = await asyncio.wait_for(
                future, timeout=self._response_timeout
            )
            return result
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            logger.warning(f"HITL request {request_id} timed out")
            return {"timed_out": True}

    # -- Callback: tool approval ---------------------------------------------

    async def _handle_approval(
        self, request: ToolApprovalRequest
    ) -> ToolApprovalResponse:
        """Called when the agent needs tool approval — routes through SSE."""
        payload = {
            "request_id": request.request_id,
            "tool_name": request.tool_name,
            "call_id": request.call_id,
            "arguments": request.arguments,
            "context": request.context,
        }

        data = await self._request_and_wait(
            "tool_approval_request", payload, request.request_id
        )

        if data.get("timed_out"):
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
