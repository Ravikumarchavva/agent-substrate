"""In-process reference implementation of :class:`TrustGraph`.

Stores a bounded ring of :class:`TrustSignal` evidence per principal and
composes a :class:`TrustScore` via signal-weighted average. Recent signals
get more weight (linear-by-age) and expired signals are skipped on read
and GC'd by :meth:`decay_expired`.

This is the *minimum useful* trust graph — a real production deployment
will plug in a Redis/Postgres backend keyed by `PrincipalId.fingerprint`
and a streaming signal ingester. The Protocol is identical so swap-out
is a one-line change in the lifespan wiring.

Thread-safety
~~~~~~~~~~~~~
All mutations of the per-principal signal lists are guarded by a single
``threading.RLock`` — race-safe under Python 3.14 free-threaded and from
background ingestion threads.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Deque

from ravi.kernel.contracts._coordination import TrustSignal
from ravi.kernel.contracts._trust import TrustScore
from ravi.kernel.runtime._identity import PrincipalId

__all__ = ["InMemoryTrustGraph", "DEFAULT_DECAY_SECONDS"]


logger = logging.getLogger(__name__)


UTC = timezone.utc
DEFAULT_DECAY_SECONDS: float = 3600.0
_DEFAULT_PER_PRINCIPAL_CAP = 128
_SOURCE_FOR_COMPOSITE = "in_memory_trust_graph"


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _signal_is_expired(signal: TrustSignal, *, now: datetime) -> bool:
    expires_at = _parse_iso(signal.expires_at)
    return expires_at is not None and expires_at <= now


class InMemoryTrustGraph:
    """In-process :class:`TrustGraph` implementation."""

    def __init__(
        self,
        *,
        per_principal_capacity: int = _DEFAULT_PER_PRINCIPAL_CAP,
        decay_seconds: float = DEFAULT_DECAY_SECONDS,
    ) -> None:
        if per_principal_capacity <= 0:
            raise ValueError(
                f"per_principal_capacity must be > 0, got {per_principal_capacity!r}"
            )
        if decay_seconds <= 0:
            raise ValueError(
                f"decay_seconds must be > 0, got {decay_seconds!r}"
            )
        self._lock = threading.RLock()
        self._signals: dict[str, Deque[TrustSignal]] = {}
        self._capacity = per_principal_capacity
        self._decay_seconds = decay_seconds

    # ----- TrustGraph Protocol -----------------------------------------

    async def ingest(
        self, principal_id: PrincipalId, signal: TrustSignal
    ) -> None:
        key = principal_id.fingerprint
        with self._lock:
            bucket = self._signals.get(key)
            if bucket is None:
                bucket = deque(maxlen=self._capacity)
                self._signals[key] = bucket
            bucket.append(signal)

    async def score_for(
        self, principal_id: PrincipalId
    ) -> TrustScore | None:
        key = principal_id.fingerprint
        now = datetime.now(UTC)
        with self._lock:
            bucket = self._signals.get(key)
            if not bucket:
                return None
            live = [s for s in bucket if not _signal_is_expired(s, now=now)]
            if not live:
                return None

            # Linear age-weight: most recent signal counts fully, oldest gets
            # 1/N weight. This is a useful default for fresh-evidence priors;
            # production backends would plug in domain-specific decay curves.
            total_weight = 0.0
            weighted_sum = 0.0
            n = len(live)
            for index, sig in enumerate(live):
                weight = (index + 1) / n  # last → 1.0, first → 1/N
                total_weight += weight
                weighted_sum += weight * sig.value
            score_value = weighted_sum / total_weight

        return TrustScore(
            value=max(0.0, min(1.0, score_value)),
            source=_SOURCE_FOR_COMPOSITE,
            computed_at=now,
            decay_seconds=self._decay_seconds,
        )

    async def signals_for(
        self, principal_id: PrincipalId
    ) -> tuple[TrustSignal, ...]:
        with self._lock:
            bucket = self._signals.get(principal_id.fingerprint)
            return tuple(bucket) if bucket else ()

    async def decay_expired(self) -> int:
        now = datetime.now(UTC)
        removed = 0
        with self._lock:
            for key, bucket in list(self._signals.items()):
                kept = deque(
                    (s for s in bucket if not _signal_is_expired(s, now=now)),
                    maxlen=self._capacity,
                )
                removed += len(bucket) - len(kept)
                if kept:
                    self._signals[key] = kept
                else:
                    del self._signals[key]
        if removed:
            logger.debug("trust_graph.decay_expired removed %d signals", removed)
        return removed

    # ----- Introspection -----------------------------------------------

    def known_principals(self) -> int:
        """Snapshot of principals currently in the graph (any active signals)."""
        with self._lock:
            return len(self._signals)
