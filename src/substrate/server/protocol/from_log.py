"""Build wire events directly from kernel run-log entries.

The kernel ``RunLogEntry.kind`` strings are, by construction, the same dotted
names as the wire protocol ``type`` discriminator (``text.delta``, ``tool.call``,
``tool.result``, …), and the entry ``payload`` carries the same fields as the
matching ``WireEvent``.  So translating a log entry to a wire event is just
deserialization — ``{"type": kind} | payload`` validated against ``WireEvent``.
There is no hand-maintained per-event mapping.

Only *streaming* kinds are surfaced here.  Run lifecycle (``run.completed`` …)
is handled by the session, which must break its tail loop and coordinate the
terminal event with persistence and the HITL bridge.
"""

from __future__ import annotations

from pydantic import TypeAdapter

from substrate.types import RunLogKind
from substrate.server.protocol.events import WireEvent

_ADAPTER: TypeAdapter[WireEvent] = TypeAdapter(WireEvent)

# Log kinds that map 1:1 to a streaming wire event.
STREAMING_KINDS = frozenset(
    {
        RunLogKind.USER_MESSAGE,
        RunLogKind.USER_MESSAGE_FLAGGED,
        RunLogKind.TEXT_DELTA,
        RunLogKind.REASONING_DELTA,
        RunLogKind.TOOL_CALL,
        RunLogKind.TOOL_RESULT,
        RunLogKind.INPUT_REQUESTED,
        RunLogKind.APPROVAL_REQUESTED,
    }
)


def wire_from_log(kind: str, payload: dict, *, history: bool = False) -> WireEvent | None:
    """Return the wire event for a log entry, or None if it isn't streamable.

    A live view reads the token stream (``text.delta``). A *history* view reads a
    finished run, where that stream is gone — it was live output, dropped after the run
    ended — so it takes the durable ``assistant.message`` instead and shows it as the
    same wire event. Either way the client sees the same events.
    """
    if history:
        if kind == RunLogKind.ASSISTANT_MESSAGE:
            return _ADAPTER.validate_python({"type": RunLogKind.TEXT_DELTA, "text": payload.get("text", "")})
        if kind in (RunLogKind.TEXT_DELTA, RunLogKind.REASONING_DELTA):
            return None
    elif kind == RunLogKind.ASSISTANT_MESSAGE:
        return None  # live viewers already have this text, token by token
    if kind not in STREAMING_KINDS:
        return None
    return _ADAPTER.validate_python({"type": kind, **payload})


__all__ = ["wire_from_log", "STREAMING_KINDS"]
