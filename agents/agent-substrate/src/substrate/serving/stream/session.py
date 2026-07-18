"""AgentStreamSession — runs one agent turn and yields wire events.

This is the single place that orchestrates a streaming chat run:

  * registers the agent with the Runtime and submits the entry Message,
  * tails the run's EventLogProtocol, turning each entry into a WireEvent via
    ``protocol.wire_from_log`` (a log entry *is* a wire event — no mapping),
  * merges out-of-band HITL / task-board events from the thread's
    ``WebHITLBridge``,
  * watches for client disconnect and explicit cancel,
  * frames the run with ``protocol.hello`` … ``run.completed|failed|cancelled``.

The route stays thin: build the agent + message + bridge, construct a session,
and stream ``session.events()`` as SSE.  All the concurrency lives here.

There is no inline persistence here anymore: the agent itself durably logs
its own conversation (``ReActAgent``'s ``log_user_message``/journaled
``ctx.llm()``/``ctx.tool()`` calls) straight to the EventLogProtocol, the single
source of truth for conversation history (see
``serving/stream/history.py::project_thread()``). This session's only
remaining job is relaying that same log, live, to one SSE connection — a
disconnect (or this process restarting) loses nothing, since the run keeps
executing durably via its own Worker-owned Task regardless of whether
anything is tailing it, and a reconnecting client re-tails the log from
wherever it left off (``tail_wire_events``) or loads the finished
conversation from ``project_thread()``.

One subtlety survives the persistence removal: ``events()`` still must not
*cancel* its own tailing task (``agent_task``) on disconnect, even though
nothing downstream of it needs to keep running for persistence's sake
anymore. ``agent_task`` calls ``register()``+``submit()`` before it ever
reaches the tail loop — cancelling it at the wrong moment could abort the
run before it's even enqueued, which is a worse outcome than anything
persistence-related ever was. So a disconnect still detaches (rather than
cancels) an in-flight ``agent_task``, exactly as before; the only thing
that's gone is the reason it used to matter.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, AsyncIterator, Awaitable, Callable

from substrate.logger import setup_logging
from substrate.serving.monolith.sse.bridge import (
    BRIDGE_DONE,
    WebHITLBridge,
    bridge_event_to_wire,
)
from substrate.serving.protocol import (
    ApprovalRequestedEvent,
    HelloEvent,
    InputRequestedEvent,
    RunCancelledEvent,
    RunCompletedEvent,
    RunFailedEvent,
    ToolResultEvent,
    WireEvent,
    wire_from_log,
)

logger = setup_logging()

DisconnectCheck = Callable[[], Awaitable[bool]]

# Strong references to agent_task instances that outlived their SSE
# connection (client disconnected before register()+submit() reached the
# tail loop). asyncio only holds a WEAK reference to a task nobody is
# awaiting, so without this a detached task could be garbage-collected
# mid-submit, silently dropping the run before it's even enqueued. Entries
# remove themselves via a done-callback (see events()'s finally) once the
# task finishes.
_DETACHED_TASKS: set[asyncio.Task] = set()


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
        self._on_complete = on_complete
        self._poll = poll_interval
        self._spec = spec
        self._queue: asyncio.Queue[WireEvent | object] = asyncio.Queue()
        self._bridge_signaled = False
        self._error: str | None = None
        self._run_id: str | None = None

    # -- workers --------------------------------------------------------------

    async def _agent_worker(self) -> str:
        """Register agent, submit message, tail EventLogProtocol. Returns terminal reason."""
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

            async for entry in self._runtime.event_log.tail(run_id):
                kind = entry.kind
                if kind == "run.completed":
                    await self._settle_task_boards()
                    return "success"
                if kind == "run.failed":
                    self._error = (entry.payload or {}).get("error", "agent run failed")
                    return "error"
                if kind == "run.cancelled":
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
                elif isinstance(wire, ApprovalRequestedEvent) and run_id:
                    # ApprovalRequestedEvent has no run_id field of its own
                    # (unlike InputRequestedEvent) — this tailing loop is
                    # always tailing exactly one run, so the loop-local
                    # run_id is correct without needing one on the wire type.
                    self._bridge.register_signal_request(
                        wire.request_id,
                        run_id,
                        card={"tool_name": wire.tool_name, "args": wire.args},
                    )
                await self._queue.put(wire)
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
            # self._queue for THIS connection — nothing depends on it once
            # this generator is done. Cancel it and await the cancellation
            # (bounded, immediate).
            if not bridge_task.done():
                bridge_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await bridge_task

            # agent_task is NOT safe to cancel here: it does
            # register()+submit() before it ever reaches the tail loop, and
            # cancelling it mid-submit could abort the run before it's even
            # enqueued (worse than anything persistence-related — the run
            # would simply never happen). The run's own execution lives in a
            # separate, Worker-owned Task regardless of agent_task's fate, so
            # once submit() has landed, cancelling this tailing loop would be
            # safe — but there's no cheap way to know we're past that point
            # from here. So: if it's already done, just check for an
            # exception; if not, detach it (a strong reference so asyncio
            # doesn't GC a task nobody's awaiting) and let it run to the
            # run's own natural completion, same as before persistence was
            # removed — it's simply relaying to a queue nobody drains
            # anymore instead of also persisting.
            if agent_task.done():
                exc = agent_task.exception()
                if exc is not None:
                    logger.error("agent_task for run %s failed: %s", self._run_id, exc)
            else:
                _DETACHED_TASKS.add(agent_task)
                agent_task.add_done_callback(_DETACHED_TASKS.discard)
                agent_task.add_done_callback(_log_detached_agent_task_exception)

        yield terminal

    # -- helpers --------------------------------------------------------------

    async def _check_disconnect(self) -> bool:
        """Return True if THIS connection should stop relaying events.

        Deliberately does NOT cancel the run just because the browser
        disconnected. A disconnect is not the same as "the user wants this
        stopped" — a page refresh disconnects too, and the entire point of
        durable suspend/resume is that a refresh must not destroy in-flight
        progress. Explicit cancel (``POST /chat/{thread_id}/cancel``) is the
        only thing that actually stops a run — it durably cancels via
        ``SupervisorProtocol.cancel()`` (see ``routes/cancel.py``), and this session
        notices that the same way it notices completion: the EventLogProtocol gets a
        ``run.cancelled`` entry, ``_agent_worker``'s tail loop sees it and
        returns. That works correctly regardless of which replica initiated
        the cancel — a local ``asyncio.Event`` (the previous design) only
        ever worked for a cancel landing on the same replica already serving
        this SSE connection.

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
        logger.error("Detached agent_task failed after disconnect: %s", exc)


async def tail_wire_events(
    event_log: Any, run_id: str, *, from_seq: int = 0
) -> AsyncIterator[WireEvent]:
    """Read-only reconnect tail: relay an ALREADY-RUNNING run's remaining
    wire events, for a browser that lost its original SSE connection
    (refresh, network drop) while the run kept executing durably server-side.

    Mirrors exactly the same terminal-kind handling
    ``AgentStreamSession._agent_worker`` uses: ``run.completed``/
    ``run.failed``/``run.cancelled`` are checked BEFORE calling
    ``wire_from_log`` (they aren't in ``STREAMING_KINDS`` — it would return
    ``None`` for them, same as any other non-wire-mapped kind like
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


__all__ = ["AgentStreamSession", "tail_wire_events"]
