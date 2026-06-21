"""AgentStreamSession — runs one agent turn and yields wire events.

This is the single place that orchestrates a streaming chat run:

  * registers the agent with the Runtime and submits the entry Message,
  * tails the run's EventLog, turning each entry into a WireEvent via
    ``protocol.wire_from_log`` (a log entry *is* a wire event — no mapping),
  * merges out-of-band HITL / task-board events from the thread's
    ``WebHITLBridge``,
  * watches for client disconnect and explicit cancel,
  * persists the assistant turn and tool results inline (optional callbacks),
  * frames the run with ``protocol.hello`` … ``run.completed|failed|cancelled``.

The route stays thin: build the agent + message + bridge, construct a session,
and stream ``session.events()`` as SSE.  All the concurrency lives here.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol

from substrate.logger import setup_logging
from substrate.serving.monolith.sse.bridge import (
    BRIDGE_DONE,
    WebHITLBridge,
    bridge_event_to_wire,
)
from substrate.serving.protocol import (
    HelloEvent,
    RunCancelledEvent,
    RunCompletedEvent,
    RunFailedEvent,
    TextDeltaEvent,
    ToolCallEvent,
    ToolCallSummary,
    ToolResultEvent,
    TurnCompletedEvent,
    WireEvent,
    wire_from_log,
)

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
        runtime: Any,
        agent: Any,
        msg: Any,
        bridge: WebHITLBridge,
        is_disconnected: DisconnectCheck | None = None,
        cancel_event: asyncio.Event | None = None,
        persister: Persister | None = None,
        on_complete: Callable[[], Awaitable[list[dict]]] | None = None,
        poll_interval: float = 0.2,
        spec: dict | None = None,
    ) -> None:
        self._runtime = runtime
        self._agent = agent
        self._msg = msg
        self._bridge = bridge
        self._is_disconnected = is_disconnected
        self._cancel = cancel_event or asyncio.Event()
        self._persister = persister
        self._on_complete = on_complete
        self._poll = poll_interval
        self._spec = spec
        self._queue: asyncio.Queue[WireEvent | object] = asyncio.Queue()
        self._bridge_signaled = False
        self._error: str | None = None
        self._run_id: str | None = None

    # -- workers --------------------------------------------------------------

    async def _agent_worker(self) -> str:
        """Register agent, submit message, tail EventLog. Returns terminal reason."""
        try:
            await self._runtime.register(self._agent)
            # max_retries=0: interactive chat runs must not be retried with the
            # same run_id — retries replay the journal and re-hit journaled errors.
            run_id = await self._runtime.submit(
                self._agent.id, self._msg, max_retries=0
            )
            self._run_id = run_id

            # Persist the agent spec so it can be rebuilt on cold resume.
            if self._spec is not None:
                scheduler = getattr(self._runtime, "_scheduler", None)
                if scheduler is not None and hasattr(scheduler, "save_run_spec"):
                    try:
                        await scheduler.save_run_spec(run_id, self._spec)
                    except Exception:
                        logger.debug("save_run_spec unavailable or failed — skipping")

            text_acc = ""
            tool_calls_acc: list[ToolCallSummary] = []
            _saw_tool_result = (
                False  # True after tool.result; flush turn on next text.delta
            )

            async def _flush_turn() -> None:
                nonlocal text_acc, tool_calls_acc, _saw_tool_result
                if self._persister and (text_acc or tool_calls_acc):
                    await self._persister.persist_turn(
                        TurnCompletedEvent(
                            text=text_acc,
                            tool_calls=tool_calls_acc,
                            finish_reason="stop",
                        )
                    )
                text_acc = ""
                tool_calls_acc = []
                _saw_tool_result = False

            async for entry in self._runtime.event_log.tail(run_id):
                kind = entry.kind
                if kind == "run.completed":
                    await _flush_turn()
                    await self._settle_task_boards()
                    return "success"
                if kind == "run.failed":
                    # Persist whatever was accumulated before the failure.
                    try:
                        await _flush_turn()
                    except Exception:
                        pass
                    self._error = (entry.payload or {}).get("error", "agent run failed")
                    return "error"
                if kind == "run.cancelled":
                    try:
                        await _flush_turn()
                    except Exception:
                        pass
                    return "cancelled"

                wire = wire_from_log(kind, entry.payload or {})
                if wire is None:
                    continue
                await self._queue.put(wire)
                if self._persister:
                    if isinstance(wire, ToolResultEvent):
                        await self._persister.persist_tool(wire)
                        _saw_tool_result = True
                    elif isinstance(wire, TextDeltaEvent):
                        # A new text.delta after tool results means a new LLM turn
                        # started — flush the completed turn before accumulating.
                        if _saw_tool_result and (text_acc or tool_calls_acc):
                            await _flush_turn()
                        text_acc += wire.text
                    elif isinstance(wire, ToolCallEvent):
                        tool_calls_acc.append(
                            ToolCallSummary(
                                id=wire.call_id,
                                name=wire.tool_name,
                                args=wire.args,
                            )
                        )
        except Exception as exc:
            logger.exception("Agent run failed")
            self._error = str(exc)
            return "error"
        finally:
            if not self._bridge_signaled:
                self._bridge_signaled = True
                await self._bridge.signal_done()
        return "success"

    async def _settle_task_boards(self) -> None:
        """On clean run completion, settle the conversation's plan boards (flip
        lingering in-progress tasks to succeeded so they stop spinning) and push
        the updated board(s) to the client. The store mutation persists so
        reloads stay settled. The settle is injected by the route as a callback
        to keep the serving layer free of an ``agents`` import."""
        if self._on_complete is None:
            return
        try:
            for board in await self._on_complete():
                await self._queue.put(
                    ToolResultEvent(
                        tool_name="manage_tasks",
                        structured_content={"task_list": board},
                    )
                )
        except Exception:
            logger.debug("task-board settle skipped", exc_info=True)

    async def _bridge_worker(self) -> None:
        """Forward HITL / task-board events until the bridge signals done."""
        while True:
            event = await self._bridge.get_event()
            if event is BRIDGE_DONE:
                break
            wire = bridge_event_to_wire(event)
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
                if await self._check_disconnect_or_cancel(agent_task):
                    terminal = RunCancelledEvent()
                    break

                try:
                    item = await asyncio.wait_for(self._queue.get(), timeout=self._poll)
                except asyncio.TimeoutError:
                    continue

                if item is _WORKERS_DONE:
                    break
                yield item  # type: ignore[misc]

            if isinstance(terminal, RunCompletedEvent):
                reason = await agent_task
                if self._error is not None:
                    terminal = RunFailedEvent(error=self._error)
                elif reason == "cancelled":
                    terminal = RunCancelledEvent()
                elif reason not in ("success", "complete"):
                    terminal = RunFailedEvent(error=reason)
        finally:
            for task in (agent_task, bridge_task):
                if not task.done():
                    task.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.gather(agent_task, bridge_task, return_exceptions=True),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Stream tasks did not finish within 5s on cleanup for run %s",
                    self._run_id,
                )

        yield terminal

    # -- helpers --------------------------------------------------------------

    async def _check_disconnect_or_cancel(self, agent_task: asyncio.Task) -> bool:
        """Return True if the run should stop (client gone or explicit cancel)."""
        disconnected = bool(self._is_disconnected and await self._is_disconnected())
        if not disconnected and not self._cancel.is_set():
            return False

        if disconnected:
            self._bridge.cancel_all_pending("session_disconnected")
        if self._run_id is not None:
            await self._runtime.cancel(self._run_id)
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
