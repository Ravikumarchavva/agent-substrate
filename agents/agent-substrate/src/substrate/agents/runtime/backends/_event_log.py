"""InMemoryEventLog — Stage 0 in-process implementation of EventLogProtocol."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import AsyncIterator

from substrate.kernel.core.errors import ConcurrentAppendError
from substrate.kernel.runtime.ids import RunId
from substrate.kernel.runtime.log_entry import RunLogEntry


class InMemoryEventLog:
    """Single-process in-memory EventLogProtocol.

    Thread-safe within a single asyncio event loop.  Not crash-durable —
    that is Stage 1 (Postgres).  Satisfies EventLogProtocol exactly,
    so Stage 1 swaps this out without touching any caller.
    """

    def __init__(self) -> None:
        self._logs: dict[RunId, list[RunLogEntry]] = defaultdict(list)
        self._waiters: dict[RunId, list[asyncio.Event]] = defaultdict(list)

    async def append(
        self, run_id: RunId, entry: RunLogEntry, *, expected_seq: int
    ) -> int:
        current = len(self._logs[run_id]) - 1
        if current != expected_seq:
            raise ConcurrentAppendError(
                f"expected seq {expected_seq}, got {current}",
                run_id=run_id,
                expected_seq=expected_seq,
                actual_seq=current,
            )
        self._logs[run_id].append(entry)
        new_seq = len(self._logs[run_id]) - 1
        for ev in self._waiters[run_id]:
            ev.set()
        self._waiters[run_id].clear()
        return new_seq

    def read(self, run_id: RunId, *, from_seq: int = 0) -> AsyncIterator[RunLogEntry]:
        return self._read_iter(run_id, from_seq)

    async def _read_iter(
        self, run_id: RunId, from_seq: int
    ) -> AsyncIterator[RunLogEntry]:  # type: ignore[return]
        for entry in self._logs[run_id][from_seq:]:
            yield entry

    def tail(self, run_id: RunId, *, from_seq: int = 0) -> AsyncIterator[RunLogEntry]:
        return self._tail_iter(run_id, from_seq)

    async def _tail_iter(
        self, run_id: RunId, from_seq: int
    ) -> AsyncIterator[RunLogEntry]:  # type: ignore[return]
        idx = from_seq
        while True:
            entries = self._logs[run_id]
            while idx < len(entries):
                yield entries[idx]
                idx += 1
            ev = asyncio.Event()
            self._waiters[run_id].append(ev)
            await ev.wait()

    async def last_seq(self, run_id: RunId) -> int:
        return len(self._logs[run_id]) - 1
