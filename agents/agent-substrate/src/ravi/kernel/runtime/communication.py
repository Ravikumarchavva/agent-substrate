"""Communication value types for the durable ask/send/reply model.

``AskOutcome`` is the discriminated result of ``RunContext.ask()``.
The four ``kind`` values are mutually exclusive and must NOT be collapsed:

    replied         — target finished and sent a reply within the timeout
    timed_out       — caller's patience expired; target is still RUNNING (lease alive)
    target_failed   — target's lease expired (worker died); safe to retry
    target_cancelled — target was explicitly cancelled (by the caller or a parent)

Collapsing ``timed_out`` with ``target_failed`` is the canonical bug that
spawns a duplicate agent while the original is still running.

``RunStatusSummary`` is the compact snapshot returned by ``RunContext.status(handle)``.
It is a batched peek — NOT a stream. The parent agent calls it rarely and only
when it has a reason (e.g., LLM tool call, supervision rule).  The human/UI
watches live progress through the SSE path (``EventLog.tail``), which is
entirely separate and never touches the parent agent's context.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from ravi.kernel.runtime.ids import RunId, RunStatus
from ravi.kernel.runtime.supervisor import RunHandle, RunResult


class AskOutcome(BaseModel):
    """Discriminated result of a RunContext.ask() call.

    Always check ``kind`` before accessing ``result`` or ``handle``.

    ``last_seq`` is the last EventLog sequence number observed for the target
    at outcome time — useful for estimating how far the target got before
    timeout or failure.
    """

    kind: Literal["replied", "timed_out", "target_failed", "target_cancelled"]
    result: RunResult | None = None  # set when kind="replied"
    handle: RunHandle | None = None  # still-live run when kind="timed_out"
    last_seq: int = -1  # target's EventLog progress

    model_config = {"frozen": True}


class RunStatusSummary(BaseModel):
    """Compact batched snapshot of a run's progress.

    Returned by ``RunContext.status(handle)`` — not a stream.
    ``last_milestone`` is the ``kind`` string of the most recent meaningful
    EventLog entry (e.g. ``"tool.result"``, ``"child.spawned"``).
    """

    run_id: RunId
    status: RunStatus
    last_seq: int
    last_milestone: str | None = None  # kind of the most recent log entry

    model_config = {"frozen": True}


__all__ = ["AskOutcome", "RunStatusSummary"]
