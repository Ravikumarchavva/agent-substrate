"""BudgetLedger — kernel contract for reservation-based spend accounting.

Every spend-bearing action follows a two-phase pattern:

1. ``reserve(principal, amount, *, ttl_seconds)`` — atomically deducts
   ``amount`` from the principal's available balance and returns a
   :class:`ReservationToken`. The token automatically expires after
   ``ttl_seconds`` so a crashed worker cannot indefinitely freeze funds.
2. ``commit(token)`` — finalises the spend (token is consumed and the
   reserved amount becomes unrecoverable). Raises :class:`ReservationLost`
   if the token has already expired or been released.

A symmetric ``release(token)`` returns the reservation back to the
balance — used when an action is aborted before it actually spends.

Why a Protocol?
~~~~~~~~~~~~~~~
Single-tenant developer setups can run an in-memory ledger. Production
deployments back it with a transactional store (Postgres, Redis with
Lua scripting). The Protocol is identical so swap-out is a one-line
change in the lifespan wiring.

Concurrency
~~~~~~~~~~~
Implementations must serialise concurrent ``reserve``/``commit``/``release``
calls so the "no over-reservation" invariant holds under any interleaving
— including the no-GIL free-threaded interleavings of Python 3.14.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ravi.kernel.runtime._identity import PrincipalId

__all__ = [
    "BudgetExhausted",
    "BudgetLedger",
    "ReservationLost",
    "ReservationToken",
]


# ---------------------------------------------------------------------------
# Value object
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReservationToken:
    """Receipt for a successful :meth:`BudgetLedger.reserve` call.

    The token is the *only* proof that the principal has set funds aside.
    Holding a token does not guarantee the reservation is still live: it
    may have expired (TTL lapsed) or already been committed/released.
    Always call :meth:`BudgetLedger.commit` to consume the reservation.
    """

    token_id: str
    principal_fqn: str
    amount: float
    granted_at: str          # ISO-8601 wall-clock
    expires_at: str          # ISO-8601 wall-clock — TTL deadline


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class BudgetExhausted(Exception):
    """Raised by :meth:`BudgetLedger.reserve` when the principal lacks balance.

    Carries enough context for routing middleware to surface a structured
    rejection to the caller (which principal, how much was requested, how
    much was actually available).
    """

    def __init__(
        self, principal_fqn: str, requested: float, available: float
    ) -> None:
        self.principal_fqn = principal_fqn
        self.requested = requested
        self.available = available
        super().__init__(
            f"budget exhausted for {principal_fqn}: "
            f"requested {requested}, available {available}"
        )


class ReservationLost(Exception):
    """Raised by :meth:`BudgetLedger.commit` when the token is no longer live.

    A reservation may be lost because:
    * its TTL elapsed before commit
    * it was already committed or released
    * the underlying store evicted it

    Callers should treat this as a fatal billing failure — the action
    that the token was supposed to authorise has no spend authority.
    """

    def __init__(self, token_id: str) -> None:
        self.token_id = token_id
        super().__init__(f"reservation {token_id} is no longer live")


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class BudgetLedger(Protocol):
    """Reservation-based spend ledger keyed by :class:`PrincipalId`.

    Implementations must guarantee:
    * ``reserve`` is atomic — no two concurrent calls can both succeed if
      together they would exceed the available balance.
    * ``commit`` and ``release`` are idempotent only on success/no-op
      paths; if the same token is committed twice the second call must
      raise :class:`ReservationLost`.
    * ``available_for`` returns ``deposited - committed - active_reservations``
      with all in-flight reservations subtracted.
    """

    async def reserve(
        self,
        principal: PrincipalId,
        amount: float,
        *,
        ttl_seconds: float = 60.0,
    ) -> ReservationToken:
        """Reserve ``amount`` against ``principal``'s balance.

        Raises :class:`BudgetExhausted` when the principal's available
        balance is below ``amount``. Raises :class:`ValueError` on
        non-positive ``ttl_seconds`` or negative ``amount``.
        """
        ...

    async def commit(self, token: ReservationToken) -> None:
        """Finalise the reservation — funds are now permanently spent.

        Raises :class:`ReservationLost` if ``token`` has expired or is
        otherwise unknown to the ledger.
        """
        ...

    async def release(self, token: ReservationToken) -> None:
        """Cancel the reservation and restore funds to the balance.

        No-op when ``token`` is unknown or already expired — releasing
        a stale token is a safe operation by design (callers shouldn't
        need to ask "did this still exist?" before cleaning up).
        """
        ...

    async def available_for(self, principal: PrincipalId) -> float:
        """Current spendable balance for ``principal``.

        Returns ``deposited - committed - active_reservations``. Unknown
        principals return ``0.0``.
        """
        ...

    async def deposit(self, principal: PrincipalId, amount: float) -> None:
        """Top up ``principal``'s balance by ``amount``.

        Intended for tests and billing/top-up flows. Raises
        :class:`ValueError` on negative ``amount``.
        """
        ...
