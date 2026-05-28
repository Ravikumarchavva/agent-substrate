"""Economic signal contracts for spend enforcement and abuse detection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from ravi.kernel.runtime._identity import PrincipalId

__all__ = [
    "EconomicSignal",
    "EconomicSignalKind",
    "EconomicSignalSource",
]


class EconomicSignalKind(Enum):
    """Known signal types emitted by economic-plane implementations."""

    BUDGET_EXHAUSTED = "budget_exhausted"
    RESERVATION_LOOP = "reservation_loop"
    RELEASE_FARMING = "release_farming"


@dataclass(frozen=True, slots=True)
class EconomicSignal:
    """A bounded, transport-neutral economic-plane warning signal."""

    signal_type: EconomicSignalKind
    principal_fqn: str
    value: float
    source_id: str
    issued_at: str
    detail: str = ""


@runtime_checkable
class EconomicSignalSource(Protocol):
    """Read-only source of economic-plane warning signals."""

    async def signals_for(
        self, principal: PrincipalId
    ) -> tuple[EconomicSignal, ...]:
        """Return current signals for ``principal``."""
        ...
