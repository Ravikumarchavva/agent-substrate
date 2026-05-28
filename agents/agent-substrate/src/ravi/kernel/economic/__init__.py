"""Economic plane — budget reservations, spend enforcement, exhaustion signals.

The economic plane is the kernel's accounting layer: every spend-bearing
action (LLM call, tool invocation, agent activation) reserves budget from a
:class:`BudgetLedger` before execution and either commits the reservation on
success or releases it on failure. This makes spend a first-class signal
the scheduler and governance planes can consume.

The kernel only ships the Protocol contract and value-objects here; concrete
in-memory / Redis / Postgres backends live in ``ravi.extensions.economic``.

Public surface::

    from ravi.kernel.economic import (
        BudgetLedger,
        BudgetExhausted,
        ReservationLost,
        ReservationToken,
    )
"""

from __future__ import annotations

from ravi.kernel.economic._ledger import (
    BudgetExhausted,
    BudgetLedger,
    ReservationLost,
    ReservationToken,
)
from ravi.kernel.economic._signals import (
    EconomicSignal,
    EconomicSignalKind,
    EconomicSignalSource,
)

__all__ = [
    "BudgetLedger",
    "BudgetExhausted",
    "EconomicSignal",
    "EconomicSignalKind",
    "EconomicSignalSource",
    "ReservationLost",
    "ReservationToken",
]
