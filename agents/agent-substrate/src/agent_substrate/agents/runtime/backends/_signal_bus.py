"""InMemorySignalBus — Stage 0 in-process implementation of SignalBus.

Also exposes ``wait_for_signal`` which is used internally by DurableContext
to implement ``ask`` and ``sleep_until_signal``.  This is NOT part of the
kernel SignalBus Protocol — it is an implementation detail of the Stage 0
in-memory runtime.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from agent_substrate.kernel.core.content import JsonObject
from agent_substrate.kernel.runtime.ids import RunId


class InMemorySignalBus:
    """Single-process in-memory SignalBus.

    Internal structure:
        _waiters[run_id][signal_name] = (asyncio.Event, payload_holder)

    ``signal()`` sets the event and fills the payload.
    ``wait_for_signal()`` awaits the event and returns the payload.
    Both ``signal`` and ``timer`` are fire-and-forget from the caller's view.
    """

    def __init__(self) -> None:
        # run_id → signal_name → (event, payload_box)
        self._waiters: dict[RunId, dict[str, tuple[asyncio.Event, list[Any]]]] = (
            defaultdict(dict)
        )

    async def signal(self, run_id: RunId, name: str, payload: JsonObject) -> None:
        slot = self._waiters[run_id].get(name)
        if slot:
            ev, box = slot
            box.clear()
            box.append(payload)
            ev.set()
        else:
            # Deliver eagerly: store so a future wait_for_signal finds it
            ev = asyncio.Event()
            box: list[Any] = [payload]
            ev.set()
            self._waiters[run_id][name] = (ev, box)

    async def timer(self, run_id: RunId, at: datetime) -> None:
        delay = max(0.0, (at - datetime.now(tz=timezone.utc)).total_seconds())
        asyncio.create_task(self._fire_after(run_id, delay))

    async def _fire_after(self, run_id: RunId, delay: float) -> None:
        await asyncio.sleep(delay)
        await self.signal(run_id, "__timer__", {})

    async def wait_for_signal(
        self,
        run_id: RunId,
        name: str,
        *,
        timeout: float | None = None,
    ) -> JsonObject:
        """Await a named signal for ``run_id``.

        Returns the payload delivered by ``signal()``.
        Raises ``asyncio.TimeoutError`` when ``timeout`` is set and expires.
        """
        slot = self._waiters[run_id].get(name)
        if slot is None:
            ev = asyncio.Event()
            box: list[Any] = []
            self._waiters[run_id][name] = (ev, box)
        else:
            ev, box = slot
            if ev.is_set() and box:
                # Already fired before we started waiting
                del self._waiters[run_id][name]
                return box[0]

        if timeout is not None:
            await asyncio.wait_for(ev.wait(), timeout=timeout)
        else:
            await ev.wait()

        self._waiters[run_id].pop(name, None)
        return box[0] if box else {}
