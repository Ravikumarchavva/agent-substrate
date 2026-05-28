"""In-memory self-evolution safeguards.

These implementations are intentionally small and infrastructure-free so
local runtimes and tests can enforce the kernel contracts without Redis or a
database. All shared mutable state is guarded by ``threading.RLock`` so the
same instances can be used from background worker threads in free-threaded
Python builds.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Deque

from ravi.guardrails.mutation import (
    BreakerSnapshot,
    BreakerState,
    CircuitOpen,
    MutationKind,
    MutationPermission,
    MutationRequest,
)

__all__ = [
    "DEFAULT_FORBIDDEN_MUTATION_KINDS",
    "InMemoryCircuitBreaker",
    "InMemoryMutationPolicy",
]


UTC = timezone.utc
DEFAULT_FORBIDDEN_MUTATION_KINDS = frozenset({MutationKind.WEIGHT_UPDATE})


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class InMemoryMutationPolicy:
    """RLock-backed :class:`MutationPolicy` implementation."""

    def __init__(
        self,
        *,
        forbidden_kinds: Iterable[MutationKind] | None = None,
        max_family_depth: int = 3,
        grant_ttl_seconds: float | None = 300.0,
        rate_limit: int = 60,
        rate_window_seconds: float = 60.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if max_family_depth < 0:
            raise ValueError("max_family_depth must be >= 0")
        if grant_ttl_seconds is not None and grant_ttl_seconds <= 0:
            raise ValueError("grant_ttl_seconds must be > 0 or None")
        if rate_limit <= 0:
            raise ValueError("rate_limit must be > 0")
        if rate_window_seconds <= 0:
            raise ValueError("rate_window_seconds must be > 0")

        self._lock = threading.RLock()
        self._forbidden_kinds = (
            frozenset(forbidden_kinds)
            if forbidden_kinds is not None
            else DEFAULT_FORBIDDEN_MUTATION_KINDS
        )
        self._max_family_depth = max_family_depth
        self._grant_ttl_seconds = grant_ttl_seconds
        self._rate_limit = rate_limit
        self._rate_window_seconds = rate_window_seconds
        self._clock = clock or _utc_now
        self._requests_by_principal: dict[str, Deque[datetime]] = {}

    async def evaluate(
        self, request: MutationRequest
    ) -> MutationPermission:
        now = _as_utc(self._clock())

        if request.kind in self._forbidden_kinds:
            return self._deny(request, now=now, reason="forbidden_kind")

        if request.family_depth < 0:
            return self._deny(request, now=now, reason="invalid_family_depth")

        if request.family_depth > self._max_family_depth:
            return self._deny(
                request, now=now, reason="family_depth_ceiling"
            )

        if not self._consume_rate_slot(request.principal_fqn, now=now):
            return self._deny(request, now=now, reason="rate_limited")

        expires_at = None
        if self._grant_ttl_seconds is not None:
            expires_at = (
                now + timedelta(seconds=self._grant_ttl_seconds)
            ).isoformat()

        return MutationPermission(
            request_id=request.request_id,
            granted=True,
            reason="granted",
            decided_at=now.isoformat(),
            expires_at=expires_at,
        )

    def _consume_rate_slot(self, principal_fqn: str, *, now: datetime) -> bool:
        cutoff = now - timedelta(seconds=self._rate_window_seconds)
        with self._lock:
            bucket = self._requests_by_principal.get(principal_fqn)
            if bucket is None:
                bucket = deque()
                self._requests_by_principal[principal_fqn] = bucket

            while bucket and bucket[0] <= cutoff:
                bucket.popleft()

            if len(bucket) >= self._rate_limit:
                return False

            bucket.append(now)
            return True

    @staticmethod
    def _deny(
        request: MutationRequest, *, now: datetime, reason: str
    ) -> MutationPermission:
        return MutationPermission(
            request_id=request.request_id,
            granted=False,
            reason=reason,
            decided_at=now.isoformat(),
            expires_at=None,
        )


@dataclass(slots=True)
class _BreakerRecord:
    state: BreakerState
    failure_count: int
    success_count: int
    updated_at: datetime
    opened_at: datetime | None = None
    half_open_probe_in_flight: bool = False


class InMemoryCircuitBreaker:
    """RLock-backed per-principal :class:`CircuitBreaker` implementation."""

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        recovery_timeout_seconds: float = 60.0,
        success_threshold: int = 1,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if failure_threshold <= 0:
            raise ValueError("failure_threshold must be > 0")
        if recovery_timeout_seconds <= 0:
            raise ValueError("recovery_timeout_seconds must be > 0")
        if success_threshold <= 0:
            raise ValueError("success_threshold must be > 0")

        self._lock = threading.RLock()
        self._failure_threshold = failure_threshold
        self._recovery_timeout_seconds = recovery_timeout_seconds
        self._success_threshold = success_threshold
        self._clock = clock or _utc_now
        self._records: dict[str, _BreakerRecord] = {}

    async def allow_request(self, principal_fqn: str) -> BreakerSnapshot:
        now = _as_utc(self._clock())
        with self._lock:
            record = self._record_for(principal_fqn, now=now)

            if record.state is BreakerState.CLOSED:
                return self._snapshot(principal_fqn, record, now=now)

            if record.state is BreakerState.OPEN:
                retry_after = self._retry_after(record, now=now)
                if retry_after <= 0:
                    record.state = BreakerState.HALF_OPEN
                    record.success_count = 0
                    record.half_open_probe_in_flight = True
                    record.updated_at = now
                    return self._snapshot(principal_fqn, record, now=now)

                snapshot = self._snapshot(principal_fqn, record, now=now)
                raise self._circuit_open(snapshot, reason="circuit_open")

            if not record.half_open_probe_in_flight:
                record.half_open_probe_in_flight = True
                record.updated_at = now
                return self._snapshot(principal_fqn, record, now=now)

            snapshot = self._snapshot(principal_fqn, record, now=now)
            raise self._circuit_open(
                snapshot, reason="half_open_probe_in_flight"
            )

    async def record_success(self, principal_fqn: str) -> BreakerSnapshot:
        now = _as_utc(self._clock())
        with self._lock:
            record = self._record_for(principal_fqn, now=now)
            record.updated_at = now

            if record.state is BreakerState.CLOSED:
                record.failure_count = 0
                record.success_count = 0
            elif record.state is BreakerState.HALF_OPEN:
                record.success_count += 1
                record.half_open_probe_in_flight = False
                if record.success_count >= self._success_threshold:
                    self._close(record, now=now)

            return self._snapshot(principal_fqn, record, now=now)

    async def record_failure(self, principal_fqn: str) -> BreakerSnapshot:
        now = _as_utc(self._clock())
        with self._lock:
            record = self._record_for(principal_fqn, now=now)
            record.failure_count += 1
            record.success_count = 0
            record.updated_at = now

            if record.state is BreakerState.OPEN:
                record.opened_at = now
            elif record.state is BreakerState.HALF_OPEN:
                self._open(record, now=now)
            elif record.failure_count >= self._failure_threshold:
                self._open(record, now=now)

            return self._snapshot(principal_fqn, record, now=now)

    async def reset(self, principal_fqn: str) -> BreakerSnapshot:
        now = _as_utc(self._clock())
        with self._lock:
            record = self._new_record(now=now)
            self._records[principal_fqn] = record
            return self._snapshot(principal_fqn, record, now=now)

    async def state_for(self, principal_fqn: str) -> BreakerSnapshot:
        now = _as_utc(self._clock())
        with self._lock:
            record = self._records.get(principal_fqn)
            if record is None:
                record = self._new_record(now=now)
            return self._snapshot(principal_fqn, record, now=now)

    def _record_for(
        self, principal_fqn: str, *, now: datetime
    ) -> _BreakerRecord:
        record = self._records.get(principal_fqn)
        if record is None:
            record = self._new_record(now=now)
            self._records[principal_fqn] = record
        return record

    @staticmethod
    def _new_record(*, now: datetime) -> _BreakerRecord:
        return _BreakerRecord(
            state=BreakerState.CLOSED,
            failure_count=0,
            success_count=0,
            updated_at=now,
        )

    def _snapshot(
        self,
        principal_fqn: str,
        record: _BreakerRecord,
        *,
        now: datetime,
    ) -> BreakerSnapshot:
        retry_after_seconds = None
        if record.state is BreakerState.OPEN:
            retry_after_seconds = self._retry_after(record, now=now)
        elif (
            record.state is BreakerState.HALF_OPEN
            and record.half_open_probe_in_flight
        ):
            retry_after_seconds = 0.0

        return BreakerSnapshot(
            principal_fqn=principal_fqn,
            state=record.state,
            failure_count=record.failure_count,
            success_count=record.success_count,
            failure_threshold=self._failure_threshold,
            success_threshold=self._success_threshold,
            updated_at=record.updated_at.isoformat(),
            opened_at=(
                record.opened_at.isoformat()
                if record.opened_at is not None
                else None
            ),
            retry_after_seconds=retry_after_seconds,
        )

    def _retry_after(
        self, record: _BreakerRecord, *, now: datetime
    ) -> float:
        if record.opened_at is None:
            return 0.0
        elapsed = (now - record.opened_at).total_seconds()
        return max(0.0, self._recovery_timeout_seconds - elapsed)

    @staticmethod
    def _circuit_open(
        snapshot: BreakerSnapshot, *, reason: str
    ) -> CircuitOpen:
        return CircuitOpen(
            principal_fqn=snapshot.principal_fqn,
            state=snapshot.state,
            reason=reason,
            opened_at=snapshot.opened_at,
            retry_after_seconds=snapshot.retry_after_seconds,
        )

    @staticmethod
    def _open(record: _BreakerRecord, *, now: datetime) -> None:
        record.state = BreakerState.OPEN
        record.opened_at = now
        record.updated_at = now
        record.success_count = 0
        record.half_open_probe_in_flight = False

    @staticmethod
    def _close(record: _BreakerRecord, *, now: datetime) -> None:
        record.state = BreakerState.CLOSED
        record.failure_count = 0
        record.success_count = 0
        record.opened_at = None
        record.updated_at = now
        record.half_open_probe_in_flight = False
