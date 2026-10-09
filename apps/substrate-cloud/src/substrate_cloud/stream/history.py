"""Conversation history — projected directly from the EventLog.

The runtime's run record is the single source of truth for a thread's conversation, not
a separately-written relational table. A thread's runs (in chronological
order — ``RuntimeStore.find_runs(thread_id=…)``) each contribute their
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

from datetime import datetime
from typing import Any

from substrate.types import RunLogKind
from substrate.runtime import NewEntry, RuntimeStore
from substrate.server.protocol.events import WireEvent
from substrate.server.protocol.from_log import wire_from_log


async def project_thread_timed(
    store: RuntimeStore, thread_id: str
) -> list[tuple[WireEvent, datetime]]:
    """The full conversation for ``thread_id`` as an ordered wire-event list — the
    canonical history read, used by the history endpoint.

    Concatenates each of the thread's runs' durable records, oldest run first, each in
    its own seq order. Entries with no streaming meaning (``run.started``,
    ``effect.result``, ``llm.call`` …) are skipped, exactly as in a live view.
    """
    events: list[tuple[WireEvent, datetime]] = []
    for run in await store.find_runs(thread_id=thread_id, active_only=False):
        for entry in await store.read_events(run.run_id, durable_only=True):
            wire = wire_from_log(entry.kind, entry.payload or {}, history=True)
            if wire is not None:
                events.append((wire, entry.ts))
    return events


async def project_thread(store: RuntimeStore, thread_id: str) -> list[WireEvent]:
    """``project_thread_timed`` without the times, for the readers that only want what was said."""
    return [event for event, _ in await project_thread_timed(store, thread_id)]


async def _annotate_thread(
    store: RuntimeStore, thread_id: str, kind: str, payload: dict[str, Any]
) -> bool:
    """Append an entry to the thread's active run, or its latest if none is active — for
    writes that do not come from a running agent (an MCP App context update, a note added
    between runs). ``False`` if the thread has no runs yet: there is nothing to attach to.
    """
    runs = await store.find_runs(thread_id=thread_id)
    if not runs:
        runs = await store.find_runs(thread_id=thread_id, active_only=False)
    if not runs:
        return False
    target = (
        runs[0]
        if runs[0].status.value in ("pending", "running", "suspended")
        else runs[-1]
    )
    await store.annotate(target.run_id, [NewEntry(kind=kind, payload=payload)])
    return True


async def append_mcp_app_context(
    store: RuntimeStore, thread_id: str, payload: dict[str, Any]
) -> None:
    """Log an interactive MCP App's context update to the thread's run. A no-op if the
    thread has no runs yet."""
    await _annotate_thread(store, thread_id, RunLogKind.MCP_APP_CONTEXT, payload)


async def append_user_message(store: RuntimeStore, thread_id: str, text: str) -> bool:
    """Log an out-of-band user message to the thread's run (feedback added to a scheduled
    task between its runs, for lookback on the next execution). ``False`` if the thread
    has no runs yet."""
    return await _annotate_thread(
        store, thread_id, RunLogKind.USER_MESSAGE, {"text": text, "attachments": []}
    )


__all__ = ["project_thread", "append_mcp_app_context", "append_user_message"]
