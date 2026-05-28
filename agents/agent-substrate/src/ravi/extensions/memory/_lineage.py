"""In-process lineage store — Section 9 reference implementation.

Thread-safe in-memory implementation of :class:`LineageStore`.  All records
live in a dict-of-dict keyed by ``session_id → message_id → LineageRecord``.
A ``threading.RLock`` guards all mutations so the store is safe from
concurrent asyncio tasks and background threads.

Causal chain traversal follows ``parent_message_id`` links and detects
cycles in O(n) via a ``seen`` set.
"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from typing import Sequence

from ravi.kernel.memory._lineage import (
    LineageNotFoundError,
    LineageRecord,
    LineageStore,
    ProvenanceTag,
    StorageTier,
)

__all__ = ["InMemoryLineageStore"]

_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
_MSG_ID_RE = re.compile(r"^[a-zA-Z0-9_:/-]{1,256}$")


def _validate_session_id(session_id: str) -> None:
    if not _SESSION_ID_RE.match(session_id):
        raise ValueError(
            f"Invalid session_id {session_id!r}; must match "
            f"{_SESSION_ID_RE.pattern}"
        )


def _validate_message_id(message_id: str) -> None:
    if not _MSG_ID_RE.match(message_id):
        raise ValueError(
            f"Invalid message_id {message_id!r}; must match "
            f"{_MSG_ID_RE.pattern}"
        )


class InMemoryLineageStore:
    """Thread-safe in-process :class:`LineageStore` implementation.

    Parameters
    ----------
    tier:
        Storage tier to declare — defaults to ``StorageTier.HOT``.
    """

    def __init__(self, *, tier: StorageTier = StorageTier.HOT) -> None:
        self._tier = tier
        self._lock = threading.RLock()
        # session_id → OrderedDict[message_id → LineageRecord] (insertion order)
        self._records: dict[str, OrderedDict[str, LineageRecord]] = {}

    # ------------------------------------------------------------------
    # LineageStore protocol
    # ------------------------------------------------------------------

    @property
    def tier(self) -> StorageTier:
        return self._tier

    async def record(
        self,
        session_id: str,
        message_id: str,
        provenance: ProvenanceTag,
    ) -> LineageRecord:
        _validate_session_id(session_id)
        _validate_message_id(message_id)
        rec = LineageRecord(
            session_id=session_id,
            message_id=message_id,
            provenance=provenance,
            tier=self._tier,
        )
        with self._lock:
            if session_id not in self._records:
                self._records[session_id] = OrderedDict()
            self._records[session_id][message_id] = rec
        return rec

    async def get(self, session_id: str, message_id: str) -> LineageRecord:
        _validate_session_id(session_id)
        _validate_message_id(message_id)
        with self._lock:
            session_records = self._records.get(session_id)
            if session_records is None or message_id not in session_records:
                raise LineageNotFoundError(
                    f"No lineage record for session={session_id!r} "
                    f"message={message_id!r}"
                )
            return session_records[message_id]

    async def list_session(
        self,
        session_id: str,
        *,
        limit: int | None = None,
    ) -> Sequence[LineageRecord]:
        _validate_session_id(session_id)
        with self._lock:
            records = list(self._records.get(session_id, {}).values())
        if limit is not None:
            records = records[-limit:]
        return records

    async def causal_chain(
        self,
        session_id: str,
        message_id: str,
    ) -> Sequence[LineageRecord]:
        _validate_session_id(session_id)
        _validate_message_id(message_id)
        with self._lock:
            session_records = self._records.get(session_id, {})
            if message_id not in session_records:
                raise LineageNotFoundError(
                    f"No lineage record for session={session_id!r} "
                    f"message={message_id!r}"
                )
            # Walk parent_message_id links to find root.
            chain: list[LineageRecord] = []
            seen: set[str] = set()
            current_id: str | None = message_id
            while current_id is not None and current_id not in seen:
                rec = session_records.get(current_id)
                if rec is None:
                    break
                seen.add(current_id)
                chain.append(rec)
                current_id = rec.provenance.parent_message_id
        chain.reverse()  # root first
        return chain

    async def drop_session(self, session_id: str) -> None:
        _validate_session_id(session_id)
        with self._lock:
            self._records.pop(session_id, None)

    # ------------------------------------------------------------------
    # Protocol runtime check helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        with self._lock:
            sessions = len(self._records)
        return f"InMemoryLineageStore(sessions={sessions}, tier={self._tier.name})"
