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
import contextlib
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol

from substrate.logger import setup_logging
from substrate.serving.monolith.sse.bridge import (
    BRIDGE_DONE,
    WebHITLBridge,
    bridge_event_to_wire,
)
from substrate.serving.protocol import (
    HelloEvent,
    InputRequestedEvent,
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

# Strong references to agent_task instances that outlived their SSE
# connection (client disconnected while the run was still running/suspended).
# asyncio only holds a WEAK reference to a task nobody is awaiting, so without
# this a detached persistence task could be garbage-collected mid-flight and
# silently stop persisting. Entries remove themselves via a done-callback
# (see events()'s finally) once the run reaches a terminal state.
_BACKGROUND_PERSIST_TASKS: set[asyncio.Task] = set()


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
        thread_id: str | None = None,
        tenant_id: str | None = None,
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
        self._thread_id = thread_id
        self._tenant_id = tenant_id
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
        from substrate.kernel.core.errors import ThreadBusyError

        try:
            await self._runtime.register(self._agent)
            # max_retries=0: interactive chat runs must not be retried with the
            # same run_id — retries replay the journal and re-hit journaled errors.
            try:
                run_id = await self._runtime.submit(
                    self._agent.id,
                    self._msg,
                    max_retries=0,
                    thread_id=self._thread_id,
                    tenant=self._tenant_id or "default",
                )
            except ThreadBusyError:
                # The route's own pre-check (find_run_for_thread) already
                # rejects the common case with a clean 409 before the SSE
                # stream even starts — this only fires on the rare race
                # where two requests for the same thread both pass that
                # check. No clean HTTP status is possible anymore (headers
                # are already sent), so this surfaces as a run.failed event
                # instead — see the generic exception handler below.
                self._error = f"thread {self._thread_id} already has an active run"
                return "error"
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
                # When a signal-based HITL card arrives, register the mapping
                # so BridgeRegistry.resolve() knows which run to signal back —
                # and cache the full card (question/context/options) so a
                # page refresh mid-suspend (GET /hitl/status/{thread_id})
                # has something to render, not just a bare request_id.
                if isinstance(wire, InputRequestedEvent) and wire.run_id and run_id:
                    self._bridge.register_signal_request(
                        wire.request_id,
                        wire.run_id,
                        card={
                            "question": wire.question,
                            "context": wire.context,
                            "options": wire.options,
                            "allow_freeform": wire.allow_freeform,
                        },
                    )
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
                if await self._check_disconnect():
                    # Nobody will ever see this event (the client is gone) —
                    # it only affects this method's own control flow below
                    # (skip the RunCompletedEvent branch, don't await
                    # agent_task). The run itself was NOT cancelled; see
                    # _check_disconnect's docstring.
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
            # bridge_task only relays local HITL/task-board events into
            # self._queue for THIS connection — nothing durable depends on
            # it. Cancel it and await the cancellation (bounded, immediate).
            if not bridge_task.done():
                bridge_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await bridge_task

            # agent_task does two things: (1) relay wire events to
            # self._queue for this connection, and (2) call
            # self._persister.persist_turn/persist_tool as turns complete —
            # the ONLY place persistence happens. The run itself is durable
            # and keeps executing via its own Worker-owned Task regardless
            # of this SSE connection's fate (the whole point of Phase 1),
            # but persistence is NOT durable on its own: it only happens
            # while something tails the EventLog. So agent_task must keep
            # running until the run reaches a terminal state even after this
            # connection closes — otherwise a run that suspends on
            # ask_human, gets answered after the browser refreshed, and then
            # completes would land its full response in the EventLog but
            # never in the steps/messages table, and a reconnecting client
            # would find nothing.
            #
            # CRITICAL: do NOT await it with a timeout here. asyncio.wait_for
            # (and asyncio.gather under it) CANCELS the awaited task when the
            # timeout fires — which would kill agent_task ~5s after
            # disconnect, i.e. before a human typically answers. That was the
            # exact bug this used to have. Instead, if the task is already
            # done we surface its result/exception; if not, we DETACH it: a
            # strong reference in a module-level set (so asyncio doesn't GC a
            # task nobody's awaiting) plus done-callbacks to clean up and log.
            if agent_task.done():
                exc = agent_task.exception()
                if exc is not None:
                    logger.error("agent_task for run %s failed: %s", self._run_id, exc)
            else:
                _BACKGROUND_PERSIST_TASKS.add(agent_task)
                agent_task.add_done_callback(_BACKGROUND_PERSIST_TASKS.discard)
                agent_task.add_done_callback(_log_detached_agent_task_exception)

        yield terminal

    # -- helpers --------------------------------------------------------------

    async def _check_disconnect(self) -> bool:
        """Return True if THIS connection should stop relaying events.

        Deliberately does NOT cancel the run (nor ``agent_task``, which also
        does the durable persistence — see ``events()``'s ``finally``
        docstring) just because the browser disconnected. A disconnect is
        not the same as "the user wants this stopped" — a page refresh
        disconnects too, and the entire point of durable suspend/resume is
        that a refresh must not destroy in-flight progress. Explicit cancel
        (``POST /chat/{thread_id}/cancel``) is the only thing that actually
        stops a run — it durably cancels via ``Supervisor.cancel()`` (see
        ``routes/cancel.py``), and this session notices that the same way
        it notices completion: the EventLog gets a ``run.cancelled`` entry,
        ``_agent_worker``'s tail loop sees it and returns. That works
        correctly regardless of which replica initiated the cancel — a
        local ``asyncio.Event`` (the previous design) only ever worked for
        a cancel landing on the same replica already serving this SSE
        connection.

        Only ``cancel_all_pending`` runs here, and only for Future-based
        tool-approval requests specifically — those aren't durable yet (see
        roadmap's "Future-based tool-approval → signal migration", still
        deferred), so a Future that nobody will ever resolve because this
        process's bridge object is about to be abandoned genuinely can't
        survive a disconnect either way; this at least fails it cleanly
        instead of leaking it forever.
        """
        disconnected = bool(self._is_disconnected and await self._is_disconnected())
        if not disconnected:
            return False

        self._bridge.cancel_all_pending("session_disconnected")
        if not self._bridge_signaled:
            self._bridge_signaled = True
            await self._bridge.signal_done()
        return True


# Sentinel: both workers finished and the queue is drained.
_WORKERS_DONE = object()


def _log_detached_agent_task_exception(task: asyncio.Task) -> None:
    """Done-callback for agent_task once it's allowed to outlive events().

    Without this, an exception raised after the SSE connection that
    originally awaited it is long gone would only ever surface as an
    unhelpful "Task exception was never retrieved" warning with no context.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Background persistence task failed after disconnect: %s", exc)


async def tail_wire_events(
    event_log: Any, run_id: str, *, from_seq: int = 0
) -> AsyncIterator[WireEvent]:
    """Read-only reconnect tail: relay an ALREADY-RUNNING run's remaining
    wire events, for a browser that lost its original SSE connection
    (refresh, network drop) while the run kept executing durably server-side.

    Deliberately does NOT persist anything (no Persister, no turn
    accumulation) — the original request's ``AgentStreamSession`` already
    owns persistence for this run via its detached ``agent_task`` (see
    ``events()``'s docstring), which keeps tailing and persisting
    independently of any UI connection until the run reaches a terminal
    state. A second tailer calling ``persist_turn``/``persist_tool`` for the
    same turns would persist them twice. This function's only job is
    "what should a reconnecting browser see next" — mirrors exactly the
    same terminal-kind handling ``AgentStreamSession._agent_worker`` uses:
    ``run.completed``/``run.failed``/``run.cancelled`` are checked BEFORE
    calling ``wire_from_log`` (they aren't in ``STREAMING_KINDS`` — it would
    return ``None`` for them, same as any other non-wire-mapped kind like
    ``run.suspended``/``run.resumed``/``llm.call``/``effect.result``, all of
    which must be silently skipped, not crash the generator).
    """
    async for entry in event_log.tail(run_id, from_seq=from_seq):
        kind = entry.kind
        if kind == "run.completed":
            yield RunCompletedEvent()
            return
        if kind == "run.failed":
            yield RunFailedEvent(
                error=(entry.payload or {}).get("error", "agent run failed")
            )
            return
        if kind == "run.cancelled":
            yield RunCancelledEvent()
            return

        wire = wire_from_log(kind, entry.payload or {})
        if wire is None:
            continue
        yield wire


__all__ = ["AgentStreamSession", "Persister", "tail_wire_events"]
