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

from typing import TYPE_CHECKING

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


__all__ = ["project_thread"]
