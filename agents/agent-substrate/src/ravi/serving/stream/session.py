"""AgentStreamSession — runs one agent turn and yields wire events.

This is the single place that orchestrates a streaming chat run:

  * starts the agent's ``run_stream()`` and maps each kernel event to a
    ``WireEvent`` (via ``stream.mapper``),
  * merges out-of-band HITL / task-board events from the thread's ``WebHITLBridge``,
  * watches for client disconnect and explicit cancel,
  * persists the assistant turn and tool results inline (optional callbacks),
  * frames the run with ``protocol.hello`` … ``run.completed|failed|cancelled``.

The route stays thin: build the agent + bridge, construct a session, and stream
``session.events()`` as SSE. All the concurrency lives here, not in the route.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol

from ravi.kernel.stream import StreamDone
from ravi.logger import setup_logging
from ravi.serving.monolith.sse.bridge import BRIDGE_DONE, WebHITLBridge
from ravi.serving.protocol import (
    HelloEvent,
    RunCancelledEvent,
    RunCompletedEvent,
    RunFailedEvent,
    TurnCompletedEvent,
    ToolResultEvent,
    WireEvent,
)
from ravi.serving.stream.mapper import map_bridge_event, map_kernel_event

logger = setup_logging()

DisconnectCheck = Callable[[], Awaitable[bool]]


class Persister(Protocol):
    """Inline persistence hooks. The route binds these to its DB session."""

    async def persist_turn(self, event: TurnCompletedEvent) -> None: ...
    async def persist_tool(self, event: ToolResultEvent) -> None: ...


class AgentStreamSession:
    def __init__(
        self,
        *,
        agent: Any,
        user_input: str,
        bridge: WebHITLBridge,
        is_disconnected: DisconnectCheck | None = None,
        cancel_event: asyncio.Event | None = None,
        persister: Persister | None = None,
        poll_interval: float = 0.2,
        initial_tool_choice: str | None = None,
    ) -> None:
        self._agent = agent
        self._input = user_input
        self._bridge = bridge
        self._is_disconnected = is_disconnected
        self._cancel = cancel_event or asyncio.Event()
        self._persister = persister
        self._poll = poll_interval
        self._initial_tool_choice = initial_tool_choice
        self._queue: asyncio.Queue[WireEvent | object] = asyncio.Queue()
        self._bridge_signaled = False
        self._error: str | None = None

    # -- workers --------------------------------------------------------------

    async def _agent_worker(self) -> str:
        """Run the agent, mapping + persisting each event. Returns terminal reason."""
        reason = "success"
        try:
            async for ev in self._agent.run_stream(
                self._input,
                initial_tool_choice=self._initial_tool_choice,
            ):
                if isinstance(ev, StreamDone):
                    reason = ev.reason
                    continue
                wire = map_kernel_event(ev)
                if wire is None:
                    continue
                for w in wire if isinstance(wire, list) else [wire]:
                    await self._queue.put(w)
                    if self._persister is not None:
                        if isinstance(w, TurnCompletedEvent):
                            await self._persister.persist_turn(w)
                        elif isinstance(w, ToolResultEvent):
                            await self._persister.persist_tool(w)
        except Exception as exc:  # agent crash → surfaced as run.failed (terminal)
            logger.exception("Agent run failed")
            self._error = str(exc)
            return "error"
        finally:
            if not self._bridge_signaled:
                self._bridge_signaled = True
                await self._bridge.signal_done()
        return reason

    async def _bridge_worker(self) -> None:
        """Forward HITL / task-board events until the bridge signals done."""
        while True:
            event = await self._bridge.get_event()
            if event is BRIDGE_DONE:
                break
            wire = map_bridge_event(event)
            if wire is not None:
                await self._queue.put(wire)
        self._queue.put_nowait(_WORKERS_DONE)

    # -- public stream --------------------------------------------------------

    async def events(self) -> AsyncIterator[WireEvent]:
        """Yield the full wire-event stream for one run."""
        yield HelloEvent()

        agent_task = asyncio.create_task(self._agent_worker())
        bridge_task = asyncio.create_task(self._bridge_worker())
        terminal: WireEvent = RunCompletedEvent()

        try:
            while True:
                try:
                    item = await asyncio.wait_for(self._queue.get(), timeout=self._poll)
                except asyncio.TimeoutError:
                    if await self._check_disconnect_or_cancel(agent_task):
                        terminal = RunCancelledEvent()
                        break
                    continue

                if item is _WORKERS_DONE:
                    break
                yield item  # type: ignore[misc]

            # Derive terminal from the agent's reason if not already cancelled.
            if isinstance(terminal, RunCompletedEvent):
                reason = await agent_task
                if self._error is not None:
                    terminal = RunFailedEvent(error=self._error)
                elif reason == "max_iterations":
                    terminal = RunCompletedEvent(reason="max_iterations")
                elif reason not in ("success", "complete"):
                    terminal = RunFailedEvent(error=reason)
        finally:
            for task in (agent_task, bridge_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(agent_task, bridge_task, return_exceptions=True)

        yield terminal

    # -- helpers --------------------------------------------------------------

    async def _check_disconnect_or_cancel(self, agent_task: asyncio.Task) -> bool:
        """Return True if the run should stop (client gone or explicit cancel)."""
        disconnected = bool(self._is_disconnected and await self._is_disconnected())
        if not disconnected and not self._cancel.is_set():
            return False

        if disconnected:
            self._bridge.cancel_all_pending("session_disconnected")
        if not agent_task.done():
            agent_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(agent_task), timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        if not self._bridge_signaled:
            self._bridge_signaled = True
            await self._bridge.signal_done()
        return True


# Sentinel: both workers finished and the queue is drained.
_WORKERS_DONE = object()


__all__ = ["AgentStreamSession", "Persister"]
