"""Tests for the in-memory economic-plane reference implementation."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from ravi.guardrails.economic import InMemoryBudgetLedger
from ravi.guardrails.economic import (
    BudgetExhausted,
    BudgetLedger,
    EconomicSignalKind,
    EconomicSignalSource,
    ReservationLost,
    ReservationToken,
)
from ravi.kernel.runtime._identity import PrincipalId, PrincipalKind


def _principal(name: str = "alice") -> PrincipalId:
    return PrincipalId(
        kind=PrincipalKind.AGENT,
        tenant_id="t1",
        workspace_id="w1",
        name=name,
    )


class TestProtocolConformance:
    async def test_isinstance_budget_ledger(self) -> None:
        ledger = InMemoryBudgetLedger()

        assert isinstance(ledger, BudgetLedger)
        assert isinstance(ledger, EconomicSignalSource)


class TestHappyPath:
    async def test_deposit_reserve_commit_updates_available_balance(self) -> None:
        ledger = InMemoryBudgetLedger()
        principal = _principal()

        await ledger.deposit(principal, 10.0)
        assert await ledger.available_for(principal) == pytest.approx(10.0)

        token = await ledger.reserve(principal, 3.5, ttl_seconds=30.0)

        assert isinstance(token, ReservationToken)
        assert token.principal_fqn == principal.fqn
        assert token.amount == pytest.approx(3.5)
        assert await ledger.available_for(principal) == pytest.approx(6.5)

        await ledger.commit(token)

        assert await ledger.available_for(principal) == pytest.approx(6.5)
        assert ledger.active_reservations() == 0

    async def test_unknown_principal_has_zero_balance(self) -> None:
        ledger = InMemoryBudgetLedger()

        assert await ledger.available_for(_principal()) == pytest.approx(0.0)


class TestExhaustion:
    async def test_reserve_raises_when_balance_is_insufficient(self) -> None:
        ledger = InMemoryBudgetLedger()
        principal = _principal()
        await ledger.deposit(principal, 1.0)

        with pytest.raises(BudgetExhausted) as exc_info:
            await ledger.reserve(principal, 2.0)

        assert exc_info.value.principal_fqn == principal.fqn
        assert exc_info.value.requested == pytest.approx(2.0)
        assert exc_info.value.available == pytest.approx(1.0)
        assert await ledger.available_for(principal) == pytest.approx(1.0)

        signals = await ledger.signals_for(principal)
        assert [s.signal_type for s in signals] == [
            EconomicSignalKind.BUDGET_EXHAUSTED
        ]


class TestExpiration:
    async def test_expired_reservation_restores_available_balance(self) -> None:
        ledger = InMemoryBudgetLedger()
        principal = _principal()
        await ledger.deposit(principal, 5.0)
        token = await ledger.reserve(principal, 4.0, ttl_seconds=0.01)

        await asyncio.sleep(0.03)

        assert await ledger.available_for(principal) == pytest.approx(5.0)
        assert ledger.active_reservations() == 0
        with pytest.raises(ReservationLost):
            await ledger.commit(token)


class TestRelease:
    async def test_release_restores_reserved_balance(self) -> None:
        ledger = InMemoryBudgetLedger()
        principal = _principal()
        await ledger.deposit(principal, 5.0)
        token = await ledger.reserve(principal, 4.0)

        await ledger.release(token)

        assert await ledger.available_for(principal) == pytest.approx(5.0)
        assert ledger.active_reservations() == 0

    async def test_release_of_unknown_or_expired_token_is_noop(self) -> None:
        ledger = InMemoryBudgetLedger()
        principal = _principal()
        await ledger.deposit(principal, 5.0)
        token = await ledger.reserve(principal, 4.0, ttl_seconds=0.01)

        await asyncio.sleep(0.03)
        await ledger.release(token)
        await ledger.release(token)

        assert await ledger.available_for(principal) == pytest.approx(5.0)


class TestTokenValidation:
    async def test_double_commit_raises_reservation_lost(self) -> None:
        ledger = InMemoryBudgetLedger()
        principal = _principal()
        await ledger.deposit(principal, 5.0)
        token = await ledger.reserve(principal, 2.0)

        await ledger.commit(token)

        with pytest.raises(ReservationLost):
            await ledger.commit(token)

    async def test_commit_rejects_mismatched_token_principal(self) -> None:
        ledger = InMemoryBudgetLedger()
        principal = _principal()
        await ledger.deposit(principal, 5.0)
        token = await ledger.reserve(principal, 2.0)
        bad_token = replace(token, principal_fqn="agent/t1/w1/bob")

        with pytest.raises(ReservationLost):
            await ledger.commit(bad_token)

        await ledger.commit(token)
        assert await ledger.available_for(principal) == pytest.approx(3.0)

    async def test_release_rejects_mismatched_token_amount(self) -> None:
        ledger = InMemoryBudgetLedger()
        principal = _principal()
        await ledger.deposit(principal, 5.0)
        token = await ledger.reserve(principal, 2.0)
        bad_token = replace(token, amount=3.0)

        with pytest.raises(ReservationLost):
            await ledger.release(bad_token)

        assert await ledger.available_for(principal) == pytest.approx(3.0)
        await ledger.release(token)
        assert await ledger.available_for(principal) == pytest.approx(5.0)


class TestInvalidValues:
    async def test_negative_deposit_rejected(self) -> None:
        ledger = InMemoryBudgetLedger()

        with pytest.raises(ValueError):
            await ledger.deposit(_principal(), -1.0)

    async def test_negative_reservation_rejected(self) -> None:
        ledger = InMemoryBudgetLedger()

        with pytest.raises(ValueError):
            await ledger.reserve(_principal(), -1.0)

    async def test_non_positive_ttl_rejected(self) -> None:
        ledger = InMemoryBudgetLedger()

        with pytest.raises(ValueError):
            await ledger.reserve(_principal(), 1.0, ttl_seconds=0.0)


class TestConcurrency:
    async def test_concurrent_reservations_do_not_overdraw(self) -> None:
        ledger = InMemoryBudgetLedger()
        principal = _principal()
        await ledger.deposit(principal, 10.0)

        def reserve_from_thread() -> ReservationToken | None:
            try:
                return asyncio.run(
                    ledger.reserve(principal, 1.0, ttl_seconds=30.0)
                )
            except BudgetExhausted:
                return None

        results = await asyncio.gather(
            *(asyncio.to_thread(reserve_from_thread) for _ in range(25))
        )
        tokens = [token for token in results if token is not None]

        assert len(tokens) == 10
        assert await ledger.available_for(principal) == pytest.approx(0.0)

        for token in tokens:
            await ledger.commit(token)

        assert await ledger.available_for(principal) == pytest.approx(0.0)


class TestEconomicSignals:
    async def test_reservation_loop_signal_is_emitted_at_threshold(self) -> None:
        ledger = InMemoryBudgetLedger(
            reservation_loop_threshold=3,
            release_farming_min_releases=10,
        )
        principal = _principal()
        await ledger.deposit(principal, 10.0)

        for _ in range(3):
            await ledger.reserve(principal, 1.0)

        signals = await ledger.signals_for(principal)
        assert [s.signal_type for s in signals] == [
            EconomicSignalKind.RESERVATION_LOOP
        ]

    async def test_release_farming_signal_is_emitted_at_threshold(self) -> None:
        ledger = InMemoryBudgetLedger(
            reservation_loop_threshold=100,
            release_farming_min_releases=2,
            release_farming_ratio=0.5,
        )
        principal = _principal()
        await ledger.deposit(principal, 10.0)

        for _ in range(2):
            token = await ledger.reserve(principal, 1.0)
            await ledger.release(token)

        signals = await ledger.signals_for(principal)
        assert [s.signal_type for s in signals] == [
            EconomicSignalKind.RELEASE_FARMING
        ]
