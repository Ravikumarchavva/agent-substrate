"""Wakeup and SignalBusProtocol — what resumes a dormant run.

A run in SUSPENDED state costs zero RAM and zero CPU.  It wakes when one of
four things happens:

    ``message``    — a message was delivered to its InboxProtocol
    ``timer``      — a wall-clock deadline has passed (ctx.sleep_until)
    ``signal``     — a named event was fired on the SignalBusProtocol
    ``child_done`` — a spawned subagent reached a terminal state

Wakeup is a sealed value object carried by the SchedulerProtocol from the event that
triggers it to the release call that records the next sleep.  It is also the
payload of the ``run.suspended`` log entry so the cause of every suspension
is replayable.

Multiple wakeup sources coalescing
-----------------------------------
If a timer fires AND a message arrives while the run is suspended, the
SchedulerProtocol coalesces them into a single wakeup and enqueues the run once —
never twice.  The order of the combined triggers is unspecified; the agent
drains its InboxProtocol and checks timers/signals during the same wake-cycle.
Implementations must honour this coalescing guarantee.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field, model_validator

from substrate.kernel.abstractions.core.content import JsonObject
from substrate.kernel.abstractions.runtime.ids import RunId


class Wakeup(BaseModel):
    """Describes why a suspended run is being woken.

    ``kind`` is one of ``"message" | "timer" | "signal" | "child_done"``.

    Fields by kind
    --------------
    message:    ``source_run`` — the Actor/RunId that sent the message (informational)
    timer:      ``at`` — the datetime that expired
    signal:     ``signals`` — the signal name(s) being waited on (a wait can watch
                more than one name at once, e.g. ``ask()`` waits on both a reply
                signal and a child-failure signal); ``payload`` — the payload of
                whichever signal actually fired, once resolved
    child_done: ``child_run`` — which child finished; ``result_ref`` — ArtifactStore
                ref where its ``RunResult`` is stored (avoids large inline payload)
    """

    kind: Literal["message", "timer", "signal", "child_done"]
    at: datetime | None = None
    signals: list[str] | None = None
    payload: JsonObject = Field(default_factory=dict)
    source_run: RunId | None = None
    child_run: RunId | None = None
    result_ref: str | None = None

    model_config = {"frozen": True}

    @model_validator(mode="after")
    def _fields_match_kind(self) -> "Wakeup":
        # One flat model with a ``kind`` tag would otherwise let a timer exist
        # with no time and a signal wait with nothing to wait for — states no
        # consumer can act on.
        if self.kind == "timer" and self.at is None:
            raise ValueError("a timer wakeup needs `at`")
        if self.kind == "signal" and not self.signals:
            raise ValueError("a signal wakeup needs at least one signal name")
        if self.kind == "child_done" and self.child_run is None:
            raise ValueError("a child_done wakeup needs `child_run`")
        return self


__all__ = ["Wakeup"]
