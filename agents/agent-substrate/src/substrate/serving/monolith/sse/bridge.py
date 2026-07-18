"""WebHITL Bridge — connects the agent's blocking HITL requests to HTTP/SSE.

The bridge is the glue between:
  - The agent (which suspends via ``ctx.sleep_until_signal()`` in the normal,
    durable case, or blocks on ``await handler.request_input()`` /
    ``await handler.request()`` in the Future-based fallback — see
    ``kernel/tools/approval.py``)
  - The HTTP layer (which streams SSE events to the frontend and
    receives responses via a separate POST endpoint)

Durable flow (both HITL kinds — tool approval and ``ask_human`` — go through
this identical path today):
  1. The agent logs an ``input.requested``/``approval.requested`` entry
     (``ctx.log_once``) and suspends via ``ctx.sleep_until_signal
     ("hitl:{request_id}")`` — see ``AskHumanTool.execute()`` and
     ``ToolInvoker._invoke_inner``'s approval branch.
  2. The monolith's run-log tailing loop (``serving/stream/session.py``)
     converts the log entry to a wire event and calls
     ``bridge.register_signal_request()`` so ``resolve()`` knows which run
     to signal back.
  3. The frontend shows a UI card and POSTs the user's response to
     ``/chat/respond/{request_id}``.
  4. The POST endpoint calls ``bridge.resolve(request_id, data)``, which
     fires ``SignalBus.signal()`` — durable, survives a process restart in
     between steps 1 and 4.
  5. The agent resumes with the response.

Future-based fallback (only when a handler is constructed with no
``signal_bus`` — tests, or a deliberately non-durable setup): steps 1 and 4
instead go through ``bridge.request_and_wait()``/an ``asyncio.Future`` held
in this process's memory, per ``_pending`` below.

Usage::

    bridge = WebHITLBridge(signal_bus=runtime.signal_bus)
    agent = ReActAgent(
        ...,
        approval_handler=SSEApprovalHandler(bridge),
        tools=[AskHumanTool(handler=bridge.human_handler), ...],
    )
"""

from __future__ import annotations
from substrate.logger import setup_logging

import asyncio
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from substrate.serving.protocol import WireEvent
    from substrate.kernel.runtime.wakeup import SignalBusProtocol

from substrate.capabilities.tools.human_input import (
    CallbackHumanHandler,
    HumanInputRequest,
    HumanInputResponse,
    HumanInputHandler,
)

logger = setup_logging()

# Sentinel used to signal the SSE generator that the agent is done
_DONE = object()

# Public alias — consumers can import BRIDGE_DONE instead of the private _DONE.
BRIDGE_DONE = _DONE


def bridge_event_to_wire(data: dict) -> "WireEvent | None":
    """Normalise a bridge HITL dict into a wire event.

    HITL events are out-of-band (they originate from the bridge, not the run
    log), and the bridge's dict shape predates the wire protocol, so this small
    adaptation point absorbs the field aliasing.  Rich tool UIs (kanban, …) flow
    inline as ``ui.resource`` via the tool result, not through here.
    """
    from substrate.serving.protocol import (
        ApprovalRequestedEvent,
        InputRequestedEvent,
        ToolResultEvent,
    )

    kind = data.get("type")
    if kind == "tool.result":
        # Subagent plan-board updates: a subagent runs as a separate run, so its
        # manage_tasks results never reach the parent run's event-log tail. The
        # tool pushes them onto this thread's bridge instead, to stream live.
        return ToolResultEvent(
            tool_name=data.get("tool_name", ""),
            structured_content=data.get("structured_content") or {},
        )
    if kind == "tool_approval_request":
        return ApprovalRequestedEvent(
            request_id=data.get("request_id") or data.get("requestId", ""),
            tool_name=data.get("tool_name", ""),
            args=data.get("arguments") or data.get("input") or {},
        )
    if kind == "human_input_request":
        return InputRequestedEvent(
            request_id=data.get("request_id") or data.get("requestId", ""),
            question=data.get("question") or data.get("prompt", ""),
            context=data.get("context", ""),
            options=data.get("options") or [],
            allow_freeform=data.get("allow_freeform", True),
        )
    return None


class WebHITLBridge:
    """Bidirectional bridge between the agent's HITL handlers and HTTP/SSE.

    Outgoing (agent → frontend):
        Events are placed on ``_outgoing`` queue.  The SSE generator
        calls ``get_event()`` to drain them.

    Incoming (frontend → agent):
        ``resolve(request_id, data)`` completes the matching Future (tool
        approval) or fires ``SignalBus.signal()`` (signal-based human input).

    ``human_handler`` is a pre-built handler instance that routes through
    this bridge. When ``signal_bus`` is provided, it's marked
    ``suspends_via_signal = True`` so ``AskHumanTool`` suspends via
    ``ctx.sleep_until_signal()`` and the event flows through the normal
    run-log tail instead of the bridge queue. Tool approval has no
    equivalent pre-built handler here — construct
    ``SSEApprovalHandler(bridge)`` (``serving/monolith/sse/approval.py``)
    and pass it directly as ``ReActAgent(approval_handler=...)``; it calls
    ``bridge.request_and_wait()`` the same way ``_handle_human_input`` does.

    Lock-free HITL:
        For Future-based approval requests the bridge stays alive in the
        registry so the user can reconnect and respond.  Signal-based
        requests are registered in ``_signal_requests`` for the same reason.
    """

    def __init__(
        self,
        response_timeout: float = 300.0,
        signal_bus: Optional["SignalBusProtocol"] = None,
    ):
        self._outgoing: asyncio.Queue[Any] = asyncio.Queue()
        self._pending: Dict[str, asyncio.Future[Dict[str, Any]]] = {}
        self._pending_payloads: Dict[str, Dict[str, Any]] = {}
        self._response_timeout = response_timeout
        self._signal_bus = signal_bus
        # request_id → run_id for signal-based human input requests
        self._signal_requests: Dict[str, str] = {}

        # Pre-built handler wired to this bridge
        self._human_handler = CallbackHumanHandler(
            callback=self._handle_human_input,
        )
        if signal_bus is not None:
            # Marker: AskHumanTool will suspend via SignalBus instead of calling
            # request_input().  The callback above becomes unreachable for the
            # human-input path when this flag is set.
            self._human_handler.suspends_via_signal = True  # type: ignore[attr-defined]

    # -- Public properties ---------------------------------------------------

    @property
    def human_handler(self) -> HumanInputHandler:
        """HumanInputHandler to pass to AskHumanTool."""
        return self._human_handler

    # -- Pending state introspection ─────────────────────────────────────

    @property
    def has_pending(self) -> bool:
        """True when at least one HITL request is awaiting user response."""
        return bool(self._pending) or bool(self._signal_requests)

    def register_signal_request(
        self, request_id: str, run_id: str, card: Optional[Dict[str, Any]] = None
    ) -> None:
        """Register a signal-based HITL request so resolve() can fire the signal.

        ``card`` (question/context/options/allow_freeform — the same fields
        the ``InputRequestedEvent`` wire event carries) is cached the same
        way ``_pending_payloads`` caches a Future-based request's payload,
        so ``get_pending_info()`` can return full card content on reconnect
        instead of just a bare ``request_id``. Without it, ``GET
        /hitl/status/{thread_id}`` returned a stub the frontend had nothing
        to render — the card silently didn't come back after a page
        refresh even though the run was still correctly suspended.
        """
        self._signal_requests[request_id] = run_id
        if card is not None:
            self._pending_payloads[request_id] = card
        logger.debug("Bridge: registered signal HITL %s → run %s", request_id, run_id)

    def get_pending_info(self) -> list[dict[str, Any]]:
        """Return metadata about all pending HITL requests.

        Used by the ``GET /hitl/status/{thread_id}`` endpoint so the frontend
        can restore approval/input cards after a reconnect.
        """
        # Future-based (tool approval) requests with full payload
        result = [
            {
                "request_id": rid,
                **(self._pending_payloads.get(rid) or {}),
            }
            for rid in self._pending
        ]
        # Signal-based (human input) requests — full card if register_signal_
        # request() was given one, else just request_id/run_id (a genuinely
        # older/incomplete registration, not expected in normal operation).
        for rid, run_id in self._signal_requests.items():
            if not any(r.get("request_id") == rid for r in result):
                result.append(
                    {
                        "request_id": rid,
                        "run_id": run_id,
                        **(self._pending_payloads.get(rid) or {}),
                    }
                )
        return result

    # -- Disconnect / cancellation -----------------------------------------------

    def cancel_all_pending(self, reason: str = "session_disconnected") -> int:
        """Resolve all pending Future-based HITL requests with a disconnect signal.

        Called when the client browser disconnects so blocked approval requests
        can resume.  Signal-based human-input requests are NOT cancelled here —
        they are killed when ``runtime.cancel(run_id)`` is called, which
        cancels the suspended asyncio Task.

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

    async def cancel_signal_requests(self, reason: str = "new_message") -> None:
        """Signal all pending signal-based HITL requests as cancelled.

        Called before starting a new run for the same thread so the old
        suspended run gets a clean ``{action: "cancelled"}`` result and can
        finish, preserving valid tool_use / tool_result pairing in history.
        """
        for request_id, run_id in list(self._signal_requests.items()):
            if self._signal_bus is not None:
                await self._signal_bus.signal(
                    run_id,
                    f"hitl:{request_id}",
                    {"action": "cancelled", "reason": reason},
                )
                logger.info(
                    "Bridge: cancelled signal HITL %s (run=%s, reason=%s)",
                    request_id,
                    run_id,
                    reason,
                )
        self._signal_requests.clear()

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

    async def resolve(self, request_id: str, data: Dict[str, Any]) -> bool:
        """Resolve a pending HITL request with the user's response.

        For signal-based human-input requests (``_signal_requests``), fires
        ``SignalBus.signal()`` to resume the suspended run.
        For Future-based tool-approval requests (``_pending``), completes the Future.

        Returns True if the request was found and resolved, False otherwise.
        """
        # Signal-based human input path
        if request_id in self._signal_requests:
            run_id = self._signal_requests.pop(request_id)
            self._pending_payloads.pop(request_id, None)
            if self._signal_bus is None:
                logger.warning(
                    "Bridge: signal HITL %s has no signal_bus — cannot resolve",
                    request_id,
                )
                return False
            await self._signal_bus.signal(run_id, f"hitl:{request_id}", data)
            logger.info("Resolved signal HITL %s (run=%s)", request_id, run_id)
            return True

        # Future-based tool-approval path
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

    # -- Request-and-wait pattern (used by SSEApprovalHandler and the
    #    human-input callback below) ------------------------------------------

    async def request_and_wait(
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

        data = await self.request_and_wait(
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

    Pass ``signal_bus`` (from ``runtime.signal_bus``) to enable signal-based
    human-input HITL.  Without it only the Future-based tool-approval path
    is available.

    Pass ``scheduler`` (from ``runtime.scheduler``) to make signal-based
    resolution work cross-replica: a ``resolve()`` call for a request_id
    with no local bridge (this replica never saw the ``input.requested``
    event, because no session is running here for that thread) falls back
    to ``Scheduler.find_run_by_wake_signal()`` — a durable query, not the
    in-process ``_signal_requests`` map any single bridge keeps.
    """

    def __init__(
        self,
        response_timeout: float = 300.0,
        signal_bus: Optional["SignalBusProtocol"] = None,
        scheduler: Optional[Any] = None,
    ) -> None:
        self._timeout = response_timeout
        self._signal_bus = signal_bus
        self._scheduler = scheduler
        self._bridges: Dict[str, "WebHITLBridge"] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, thread_id: str) -> "WebHITLBridge":
        """Return the live bridge for *thread_id*, creating one if needed."""
        async with self._lock:
            if thread_id not in self._bridges:
                self._bridges[thread_id] = WebHITLBridge(
                    self._timeout, signal_bus=self._signal_bus
                )
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

    async def resolve(self, request_id: str, data: Dict[str, Any]) -> bool:
        """Find the bridge that owns *request_id* and resolve it.

        Scans all active bridges.  Because request IDs are UUIDs, collisions
        are statistically impossible across bridges.

        Future-based tool-approval requests only ever exist locally (the
        Future lives in this process), so a miss there is final. Signal-based
        human-input requests fall back to a durable, cross-replica lookup —
        this POST may be landing on a different replica than the one whose
        session registered ``request_id`` in a bridge's ``_signal_requests``
        (that registration only happens as a side effect of *this* replica
        having tailed the ``input.requested`` event — see
        ``AgentStreamSession``), so the SignalBus's own durable state
        (``Scheduler.find_run_by_wake_signal``) is the source of truth, not
        any bridge's local map.
        """
        for bridge in list(self._bridges.values()):
            if request_id in bridge._pending or request_id in bridge._signal_requests:
                return await bridge.resolve(request_id, data)

        if self._scheduler is not None and self._signal_bus is not None:
            run_id = await self._scheduler.find_run_by_wake_signal(f"hitl:{request_id}")
            if run_id is not None:
                await self._signal_bus.signal(run_id, f"hitl:{request_id}", data)
                logger.info(
                    "BridgeRegistry: resolved request_id=%s durably (run=%s, "
                    "no local bridge owned it)",
                    request_id,
                    run_id,
                )
                return True

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
