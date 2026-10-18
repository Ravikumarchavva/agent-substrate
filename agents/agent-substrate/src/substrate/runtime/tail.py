"""Following a run's record as it grows."""

from __future__ import annotations

from collections.abc import AsyncIterator

from substrate.types.run_status import RunId
from substrate.types.run_log import RunLogEntry
from substrate.runtime.store import RuntimeStore

_WAIT_S = 1.0


async def tail(
    store: RuntimeStore, run_id: RunId | str, *, from_seq: int = 0
) -> AsyncIterator[RunLogEntry]:
    """A run's entries — live output included — as they are written.

    Never ends on its own: the caller stops iterating at the terminal entry it cares
    about. Idle waiting is bounded (``store.wait_events``), so a write by another process,
    which no in-process event can announce, is still seen promptly.
    """
    run_id = RunId(run_id)
    seq = from_seq
    while True:
        entries = await store.read_events(run_id, from_seq=seq)
        for entry in entries:
            yield entry
            seq = entry.seq + 1
        if not entries:
            await store.wait_events(run_id, after_seq=seq - 1, timeout_s=_WAIT_S)


__all__ = ["tail"]
