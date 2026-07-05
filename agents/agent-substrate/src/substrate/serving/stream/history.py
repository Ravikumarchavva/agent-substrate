"""Conversation history — projected directly from the EventLog.

The EventLog is the single source of truth for a thread's conversation, not
a separately-written relational table. A thread's runs (in chronological
order — ``Scheduler.find_all_runs_for_thread()``) each contribute their
streaming wire events (``user.message``, ``text.delta``, ``tool.call``,
``tool.result``, ``input.requested`` — the same ``STREAMING_KINDS``
``wire_from_log`` maps for live streaming and reconnect) to one flat,
chronological event list. Live streaming (``AgentStreamSession``), reconnect
(``tail_wire_events``), and history (``project_thread``) are three views over
the exact same underlying data, through the exact same ``wire_from_log``
mapping — there is no second, independently-maintained store to drift from
the log.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from substrate.kernel.core.errors import ConcurrentAppendError
from substrate.kernel.runtime.log_entry import RunLogEntry
from substrate.serving.protocol.events import WireEvent
from substrate.serving.protocol.from_log import wire_from_log

if TYPE_CHECKING:
    from substrate.kernel.runtime.log_entry import EventLog
    from substrate.kernel.runtime.scheduler import Scheduler


async def project_thread(
    event_log: "EventLog", scheduler: "Scheduler", thread_id: str
) -> list[WireEvent]:
    """Return the full conversation for ``thread_id`` as an ordered wire-event
    list — the canonical history read, used by the history endpoint and by
    memory-seeding (via a sibling projection over ``ChatMessage`` instead of
    wire events; see ``agents/factory.py``).

    Concatenates each of the thread's runs' EventLogs, oldest run first,
    each read in its own seq order. Skips any log entry ``wire_from_log``
    doesn't map to a streaming event (``run.started``, ``effect.result``,
    ``llm.call``, ...) — exactly the same filtering live streaming applies.
    """
    events: list[WireEvent] = []
    run_ids = await scheduler.find_all_runs_for_thread(thread_id)
    for run_id in run_ids:
        async for entry in event_log.read(run_id):
            wire = wire_from_log(entry.kind, entry.payload or {})
            if wire is not None:
                events.append(wire)
    return events


async def _append_to_thread(
    event_log: "EventLog",
    scheduler: "Scheduler",
    thread_id: str,
    kind: str,
    payload: dict[str, Any],
) -> bool:
    """Append a log entry to the thread's active run, or its most recent run
    if none is active — for out-of-band writes that don't originate from a
    running agent (an MCP App context update, a note added between runs).

    Retries on ``ConcurrentAppendError`` (the target run may have concurrent
    activity, e.g. the agent itself appending) by reloading the log's current
    tail and re-attempting. Returns ``False`` if the thread has no runs at
    all yet (nothing to attach to).
    """
    active = await scheduler.find_run_for_thread(thread_id)
    if active is not None:
        run_id = active[0]
    else:
        run_ids = await scheduler.find_all_runs_for_thread(thread_id)
        if not run_ids:
            return False
        run_id = run_ids[-1]

    while True:
        last_seq = -1
        async for entry in event_log.read(run_id):
            last_seq = entry.seq
        entry = RunLogEntry(run_id=run_id, seq=last_seq + 1, kind=kind, payload=payload)
        try:
            await event_log.append(run_id, entry, expected_seq=last_seq)
            return True
        except ConcurrentAppendError:
            continue


async def append_mcp_app_context(
    event_log: "EventLog",
    scheduler: "Scheduler",
    thread_id: str,
    payload: dict[str, Any],
) -> None:
    """Log an interactive MCP App's context update to the thread's run.

    See ``_append_to_thread`` for attach/retry semantics. No-ops if the
    thread has no runs at all yet.
    """
    await _append_to_thread(event_log, scheduler, thread_id, "mcp_app_context", payload)


async def append_user_message(
    event_log: "EventLog", scheduler: "Scheduler", thread_id: str, text: str
) -> bool:
    """Log an out-of-band user message to the thread's run (e.g. feedback
    added to a scheduled task between its runs, for lookback context on the
    next execution).

    See ``_append_to_thread`` for attach/retry semantics. Returns ``False``
    if the thread has no runs at all yet — there is nothing to attach to.
    """
    return await _append_to_thread(
        event_log,
        scheduler,
        thread_id,
        "user.message",
        {"text": text, "attachments": []},
    )


__all__ = ["project_thread", "append_mcp_app_context", "append_user_message"]
