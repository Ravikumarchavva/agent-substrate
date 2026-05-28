"""Tests for economic-plane kernel value objects."""

from __future__ import annotations

from ravi.kernel.economic import (
    BudgetExhausted,
    EconomicSignal,
    EconomicSignalKind,
)


def test_budget_exhausted_carries_context() -> None:
    exc = BudgetExhausted("agent/t1/w1/alice", requested=2.5, available=1.0)

    assert exc.principal_fqn == "agent/t1/w1/alice"
    assert exc.requested == 2.5
    assert exc.available == 1.0
    assert "requested 2.5" in str(exc)


def test_economic_signal_is_transport_neutral_value_object() -> None:
    signal = EconomicSignal(
        signal_type=EconomicSignalKind.RESERVATION_LOOP,
        principal_fqn="agent/t1/w1/alice",
        value=1.0,
        source_id="test",
        issued_at="2026-05-28T00:00:00+00:00",
        detail="reservations=3",
    )

    assert signal.signal_type is EconomicSignalKind.RESERVATION_LOOP
    assert signal.principal_fqn == "agent/t1/w1/alice"
    assert signal.detail == "reservations=3"
