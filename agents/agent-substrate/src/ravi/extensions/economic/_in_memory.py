"""In-process implementation of the kernel economic-plane contracts.

The ledger is intentionally small: all shared mutable accounting state is
guarded by one ``threading.RLock`` so reserve/commit/release operations stay
atomic under free-threaded Python and background worker threads.
"""

from __future__ import annotations

import math
import threading
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ravi.kernel.economic import (
    BudgetExhausted,
    BudgetLedger,
    EconomicSignal,
    EconomicSignalKind,
    EconomicSignalSource,
    ReservationLost,
    ReservationToken,
)
from ravi.kernel.runtime._identity import PrincipalId

__all__ = ["InMemoryBudgetLedger"]


UTC = timezone.utc
_SOURCE_ID = "in_memory_budget_ledger"
_DEFAULT_SIGNAL_WINDOW_SECONDS = 60.0
_DEFAULT_RESERVATION_LOOP_THRESHOLD = 100
_DEFAULT_RELEASE_FARMING_MIN_RELEASES = 50
_DEFAULT_RELEASE_FARMING_RATIO = 0.8
_DEFAULT_SIGNAL_CAPACITY = 64


@dataclass(frozen=True, slots=True)
class _Reservation:
    principal_fqn: str
    amount: float
    granted_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class _SpendEvent:
    kind: str
    occurred_at: datetime


class InMemoryBudgetLedger(BudgetLedger, EconomicSignalSource):
    """Free-threading-safe in-memory :class:`BudgetLedger`.

    This is a reference implementation for tests and single-process
    deployments. Production backends should keep the same token semantics but
    move the atomic sections into a transactional data store.
    """

    def __init__(
        self,
        *,
        signal_window_seconds: float = _DEFAULT_SIGNAL_WINDOW_SECONDS,
        reservation_loop_threshold: int = _DEFAULT_RESERVATION_LOOP_THRESHOLD,
        release_farming_min_releases: int = _DEFAULT_RELEASE_FARMING_MIN_RELEASES,
        release_farming_ratio: float = _DEFAULT_RELEASE_FARMING_RATIO,
        signal_capacity: int = _DEFAULT_SIGNAL_CAPACITY,
    ) -> None:
        if signal_window_seconds <= 0 or not math.isfinite(signal_window_seconds):
            raise ValueError("signal_window_seconds must be finite and > 0")
        if reservation_loop_threshold <= 0:
            raise ValueError("reservation_loop_threshold must be > 0")
        if release_farming_min_releases <= 0:
            raise ValueError("release_farming_min_releases must be > 0")
        if not 0.0 < release_farming_ratio <= 1.0:
            raise ValueError("release_farming_ratio must be in (0.0, 1.0]")
        if signal_capacity <= 0:
            raise ValueError("signal_capacity must be > 0")

        self._lock = threading.RLock()
        self._deposited: dict[str, float] = {}
        self._committed: dict[str, float] = {}
        self._reserved: dict[str, float] = {}
        self._reservations: dict[str, _Reservation] = {}
        self._events: dict[str, deque[_SpendEvent]] = {}
        self._signals: dict[str, deque[EconomicSignal]] = {}
        self._signal_window = timedelta(seconds=signal_window_seconds)
        self._reservation_loop_threshold = reservation_loop_threshold
        self._release_farming_min_releases = release_farming_min_releases
        self._release_farming_ratio = release_farming_ratio
        self._signal_capacity = signal_capacity

    async def reserve(
        self,
        principal: PrincipalId,
        amount: float,
        *,
        ttl_seconds: float = 60.0,
    ) -> ReservationToken:
        _validate_amount(amount, label="amount")
        if ttl_seconds <= 0 or not math.isfinite(ttl_seconds):
            raise ValueError("ttl_seconds must be finite and > 0")

        now = _utc_now()
        principal_fqn = principal.fqn
        expires_at = now + timedelta(seconds=ttl_seconds)
        token_id = uuid.uuid4().hex

        with self._lock:
            self._expire_locked(now=now)
            available = self._available_for_locked(principal_fqn)
            if available < amount:
                self._emit_signal_locked(
                    principal_fqn=principal_fqn,
                    kind=EconomicSignalKind.BUDGET_EXHAUSTED,
                    value=1.0 if amount > 0 else 0.0,
                    now=now,
                    detail=f"requested={amount} available={available}",
                )
                raise BudgetExhausted(principal_fqn, amount, available)

            self._reservations[token_id] = _Reservation(
                principal_fqn=principal_fqn,
                amount=amount,
                granted_at=now,
                expires_at=expires_at,
            )
            self._reserved[principal_fqn] = (
                self._reserved.get(principal_fqn, 0.0) + amount
            )
            self._record_event_locked(
                principal_fqn=principal_fqn,
                kind="reserve",
                now=now,
            )

        return ReservationToken(
            token_id=token_id,
            principal_fqn=principal_fqn,
            amount=amount,
            granted_at=now.isoformat(),
            expires_at=expires_at.isoformat(),
        )

    async def commit(self, token: ReservationToken) -> None:
        now = _utc_now()
        with self._lock:
            self._expire_locked(now=now)
            reservation = self._reservations.get(token.token_id)
            if reservation is None:
                raise ReservationLost(token.token_id)
            if not _token_matches(token, reservation):
                raise ReservationLost(token.token_id)

            del self._reservations[token.token_id]
            self._reserved[reservation.principal_fqn] = max(
                0.0,
                self._reserved.get(reservation.principal_fqn, 0.0)
                - reservation.amount,
            )
            self._committed[reservation.principal_fqn] = (
                self._committed.get(reservation.principal_fqn, 0.0)
                + reservation.amount
            )
            self._record_event_locked(
                principal_fqn=reservation.principal_fqn,
                kind="commit",
                now=now,
            )

    async def release(self, token: ReservationToken) -> None:
        now = _utc_now()
        with self._lock:
            self._expire_locked(now=now)
            reservation = self._reservations.get(token.token_id)
            if reservation is None:
                return
            if not _token_matches(token, reservation):
                raise ReservationLost(token.token_id)

            del self._reservations[token.token_id]
            self._reserved[reservation.principal_fqn] = max(
                0.0,
                self._reserved.get(reservation.principal_fqn, 0.0)
                - reservation.amount,
            )
            self._record_event_locked(
                principal_fqn=reservation.principal_fqn,
                kind="release",
                now=now,
            )

    async def available_for(self, principal: PrincipalId) -> float:
        now = _utc_now()
        with self._lock:
            self._expire_locked(now=now)
            return self._available_for_locked(principal.fqn)

    async def deposit(self, principal: PrincipalId, amount: float) -> None:
        _validate_amount(amount, label="amount")
        with self._lock:
            principal_fqn = principal.fqn
            self._deposited[principal_fqn] = (
                self._deposited.get(principal_fqn, 0.0) + amount
            )

    async def signals_for(
        self, principal: PrincipalId
    ) -> tuple[EconomicSignal, ...]:
        now = _utc_now()
        with self._lock:
            self._expire_events_locked(principal.fqn, now=now)
            bucket = self._signals.get(principal.fqn)
            return tuple(bucket) if bucket else ()

    def active_reservations(self) -> int:
        """Return a snapshot count of live reservations."""
        now = _utc_now()
        with self._lock:
            self._expire_locked(now=now)
            return len(self._reservations)

    def _available_for_locked(self, principal_fqn: str) -> float:
        deposited = self._deposited.get(principal_fqn, 0.0)
        committed = self._committed.get(principal_fqn, 0.0)
        reserved = self._reserved.get(principal_fqn, 0.0)
        return max(0.0, deposited - committed - reserved)

    def _expire_locked(self, *, now: datetime) -> None:
        for token_id, reservation in list(self._reservations.items()):
            if reservation.expires_at <= now:
                del self._reservations[token_id]
                self._reserved[reservation.principal_fqn] = max(
                    0.0,
                    self._reserved.get(reservation.principal_fqn, 0.0)
                    - reservation.amount,
                )

    def _record_event_locked(
        self,
        *,
        principal_fqn: str,
        kind: str,
        now: datetime,
    ) -> None:
        events = self._events.get(principal_fqn)
        if events is None:
            events = deque()
            self._events[principal_fqn] = events
        events.append(_SpendEvent(kind=kind, occurred_at=now))
        self._expire_events_locked(principal_fqn, now=now)
        self._maybe_emit_pattern_signals_locked(
            principal_fqn=principal_fqn,
            events=events,
            now=now,
        )

    def _expire_events_locked(self, principal_fqn: str, *, now: datetime) -> None:
        events = self._events.get(principal_fqn)
        if events is None:
            return
        cutoff = now - self._signal_window
        while events and events[0].occurred_at < cutoff:
            events.popleft()
        if not events:
            del self._events[principal_fqn]

    def _maybe_emit_pattern_signals_locked(
        self,
        *,
        principal_fqn: str,
        events: deque[_SpendEvent],
        now: datetime,
    ) -> None:
        reservations = sum(1 for event in events if event.kind == "reserve")
        releases = sum(1 for event in events if event.kind == "release")

        if reservations == self._reservation_loop_threshold:
            self._emit_signal_locked(
                principal_fqn=principal_fqn,
                kind=EconomicSignalKind.RESERVATION_LOOP,
                value=1.0,
                now=now,
                detail=f"reservations={reservations}",
            )

        release_ratio = releases / reservations if reservations else 0.0
        if (
            releases == self._release_farming_min_releases
            and release_ratio >= self._release_farming_ratio
        ):
            self._emit_signal_locked(
                principal_fqn=principal_fqn,
                kind=EconomicSignalKind.RELEASE_FARMING,
                value=release_ratio,
                now=now,
                detail=f"releases={releases} reservations={reservations}",
            )

    def _emit_signal_locked(
        self,
        *,
        principal_fqn: str,
        kind: EconomicSignalKind,
        value: float,
        now: datetime,
        detail: str,
    ) -> None:
        bucket = self._signals.get(principal_fqn)
        if bucket is None:
            bucket = deque(maxlen=self._signal_capacity)
            self._signals[principal_fqn] = bucket
        bucket.append(
            EconomicSignal(
                signal_type=kind,
                principal_fqn=principal_fqn,
                value=max(0.0, min(1.0, value)),
                source_id=_SOURCE_ID,
                issued_at=now.isoformat(),
                detail=detail,
            )
        )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _validate_amount(amount: float, *, label: str) -> None:
    if amount < 0 or not math.isfinite(amount):
        raise ValueError(f"{label} must be finite and >= 0")


def _token_matches(token: ReservationToken, reservation: _Reservation) -> bool:
    return (
        token.principal_fqn == reservation.principal_fqn
        and token.amount == reservation.amount
    )
