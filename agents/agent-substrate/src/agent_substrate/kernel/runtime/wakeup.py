"""Wakeup and SignalBus — what resumes a dormant run.

A run in SUSPENDED state costs zero RAM and zero CPU.  It wakes when one of
four things happens:

    ``message``    — a message was delivered to its Inbox
    ``timer``      — a wall-clock deadline has passed (ctx.sleep_until)
    ``signal``     — a named event was fired on the SignalBus
    ``child_done`` — a spawned subagent reached a terminal state

Wakeup is a sealed value object carried by the Scheduler from the event that
triggers it to the release call that records the next sleep.  It is also the
payload of the ``run.suspended`` log entry so the cause of every suspension
is replayable.

Multiple wakeup sources coalescing
-----------------------------------
If a timer fires AND a message arrives while the run is suspended, the
Scheduler coalesces them into a single wakeup and enqueues the run once —
never twice.  The order of the combined triggers is unspecified; the agent
drains its Inbox and checks timers/signals during the same wake-cycle.
Implementations must honour this coalescing guarantee.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from agent_substrate.kernel.core.content import JsonObject
from agent_substrate.kernel.runtime.ids import RunId


class Wakeup(BaseModel):
    """Describes why a suspended run is being woken.

    ``kind`` is one of ``"message" | "timer" | "signal" | "child_done"``.

    Fields by kind
    --------------
    message:    ``source_run`` — the AgentId/RunId that sent the message (informational)
    timer:      ``at`` — the datetime that expired
    signal:     ``signal`` — the signal name; ``payload`` — signal data
    child_done: ``child_run`` — which child finished; ``result_ref`` — ArtifactStore
                ref where its ``RunResult`` is stored (avoids large inline payload)
    """

    kind: Literal["message", "timer", "signal", "child_done"]
    at: datetime | None = None
    signal: str | None = None
    payload: JsonObject = Field(default_factory=dict)
    source_run: RunId | None = None
    child_run: RunId | None = None
    result_ref: str | None = None

    model_config = {"frozen": True}


class SignalBus(Protocol):
    """Contract for sending named signals and timers to suspended runs.

    Signals are lightweight — they carry a small JSON payload and wake a
    specific run by name.  They are the mechanism behind ``ctx.wait_signal()``
    and ``ctx.sleep_until()``.

    Implementations: in-memory asyncio.Event dict (Stage 0), Redis pub/sub
    with run_id channel (Stage 1+).

    Semantic guarantees
    -------------------
    - A signal fired before the run suspends is not lost — it is buffered and
      delivered as the wakeup trigger when the run next calls ``suspend``.
    - ``timer`` is best-effort with millisecond granularity; the implementation
      may fire up to a few seconds late under load.  Agents must not rely on
      precise wall-clock accuracy for correctness.
    """

    async def signal(
        self,
        run_id: RunId,
        name: str,
        payload: JsonObject,
    ) -> None:
        """Fire a named signal at ``run_id``.

        Wakes a suspended run that is waiting on this signal name.
        If the run is not currently suspended, the signal is buffered.
        """
        ...

    async def timer(self, run_id: RunId, at: datetime) -> None:
        """Schedule a timer wakeup for ``run_id`` at wall-clock time ``at``.

        If ``at`` is in the past, the wakeup fires immediately.
        Cancelling the run before ``at`` cancels the pending timer.
        """
        ...


__all__ = ["Wakeup", "SignalBus"]
