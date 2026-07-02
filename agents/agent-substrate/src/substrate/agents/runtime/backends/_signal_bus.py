"""InMemorySignalBus — Stage 0 in-process implementation of SignalBus.

Consume-based, matching the durable (Postgres) backend's semantics exactly:
``signal()`` buffers a payload and wakes the target run via the Scheduler if
it's suspended and waiting on that name; ``consume()`` claims one buffered
payload, exactly-once per ``effect_id`` (idempotent re-claim on replay).
There is no blocking wait here — suspension is achieved by the caller
raising ``SuspendInterrupt`` when ``consume()`` returns ``None``, not by
parking a coroutine on this bus. That's what makes a SUSPENDED run genuinely
zero-cost: no asyncio Task, no Event, nothing — the Worker's Task has
already ended by the time this bus is holding a buffered-but-unclaimed
signal.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from substrate.kernel.core.content import JsonObject
from substrate.kernel.runtime.ids import RunId

if TYPE_CHECKING:
    from substrate.agents.runtime.backends._scheduler import InMemoryScheduler


class InMemorySignalBus:
    """Single-process in-memory SignalBus, consume-based.

    Internal structure:
        _buffered[run_id][name] = [payload, payload, ...]   (FIFO per name)
        _consumed[effect_id]    = payload                    (idempotency record)
    """

    def __init__(self, scheduler: InMemoryScheduler) -> None:
        self._scheduler = scheduler
        self._buffered: dict[RunId, dict[str, list[JsonObject]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self._consumed: dict[str, JsonObject] = {}
        self._timer_tasks: dict[RunId, asyncio.Task] = {}

    async def signal(self, run_id: RunId, name: str, payload: JsonObject) -> None:
        self._buffered[run_id][name].append(payload)
        wakeup = self._scheduler.wakeup_for(run_id)
        if wakeup is not None and wakeup.kind == "signal" and wakeup.signals:
            if name in wakeup.signals:
                await self._scheduler.wake_suspended(run_id)

    async def consume(
        self, run_id: RunId, name: str, effect_id: str
    ) -> JsonObject | None:
        if effect_id in self._consumed:
            return self._consumed[effect_id]
        bucket = self._buffered.get(run_id, {}).get(name)
        if bucket:
            payload = bucket.pop(0)
            self._consumed[effect_id] = payload
            return payload
        return None

    async def timer(self, run_id: RunId, at: datetime) -> None:
        """Arrange a wake-up at wall-clock time ``at``.

        Stage 0 has real async timers available, so this spawns a task that
        sleeps then directly wakes the suspended run — no polling needed
        in-process. (The durable Postgres backend instead sets a ``wake_at``
        column and relies on the scheduler's existing lease-poll cadence,
        since a DB row can't sleep — see infrastructure/runtime/pg_signal_bus.py.)
        """
        old = self._timer_tasks.pop(run_id, None)
        if old is not None and not old.done():
            old.cancel()
        delay = max(0.0, (at - datetime.now(tz=timezone.utc)).total_seconds())
        self._timer_tasks[run_id] = asyncio.create_task(self._fire_after(run_id, delay))

    async def _fire_after(self, run_id: RunId, delay: float) -> None:
        await asyncio.sleep(delay)
        await self._scheduler.wake_suspended(run_id)

    def gc(self, run_id: RunId) -> None:
        """Drop a terminal run's buffered-but-unclaimed signals and pending timer.

        A terminal run will never call ``consume()`` again, so anything
        still sitting in ``_buffered[run_id]`` (e.g. a late ``ask()`` reply
        nobody's waiting on anymore) is dead weight — left uncollected, a
        long-lived Runtime accumulates one dict entry per never-consumed
        signal for the life of the process. ``_consumed`` is intentionally
        left alone: it's keyed by opaque effect_id, not run_id, so there's
        no cheap way to scope a deletion to just this run, and it's bounded
        by total distinct effects ever created rather than message volume.
        """
        self._buffered.pop(run_id, None)
        task = self._timer_tasks.pop(run_id, None)
        if task is not None and not task.done():
            task.cancel()
