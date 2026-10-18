"""What a suspended conversation is waiting for a person to decide, read from the run's durable journal.

A run that needs a person (a tool to approve, a question to answer) is suspended in the runtime's store, so this survives restarts
and reads the same in the monolith and in the human-gate service.
"""

from __future__ import annotations

from typing import Any

from substrate.types import RunLogKind, RunStatus


async def pending_cards(runtime: Any, thread_id: str) -> list[dict]:
    """The approval or question card the thread's suspended run is waiting on (none when it is not suspended)."""
    found = await runtime.active_run_for_thread(thread_id)
    if found is None or found.status != RunStatus.SUSPENDED:
        return []
    run_id = found.run_id

    last_kind: str | None = None
    last_request: dict | None = None
    for entry in await runtime.read(run_id):
        if entry.kind in (RunLogKind.INPUT_REQUESTED, RunLogKind.APPROVAL_REQUESTED):
            last_kind = entry.kind
            last_request = entry.payload
    if last_request is None or last_kind is None:
        return []

    request_id = last_request.get("request_id", "")
    if last_kind == RunLogKind.INPUT_REQUESTED:
        return [
            {
                "request_id": request_id,
                "run_id": run_id,
                "question": last_request.get("question", ""),
                "context": last_request.get("context", ""),
                "options": last_request.get("options", []),
                "allow_freeform": last_request.get("allow_freeform", True),
            }
        ]
    return [
        {
            "request_id": request_id,
            "run_id": run_id,
            "tool_name": last_request.get("tool_name", ""),
            "args": last_request.get("args", {}),
        }
    ]
