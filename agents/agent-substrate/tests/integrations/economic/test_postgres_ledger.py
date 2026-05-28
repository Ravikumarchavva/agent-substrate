"""Tests for PostgresBudgetLedger.

All DB I/O is mocked — no live Postgres server required.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ravi.integrations.economic import PostgresBudgetLedger
from ravi.integrations.economic._postgres_ledger import (
    EconomicBalance,
    EconomicReservation,
    EconomicSignalRow,
)
from ravi.kernel.economic import (
    BudgetExhausted,
    BudgetLedger,
    EconomicSignalKind,
    EconomicSignalSource,
    ReservationLost,
    ReservationToken,
)
from ravi.kernel.runtime._identity import PrincipalId, PrincipalKind

_UTC = timezone.utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _principal(name: str = "alice") -> PrincipalId:
    return PrincipalId(
        kind=PrincipalKind.HUMAN,
        tenant_id="tenant-1",
        workspace_id="ws-1",
        name=name,
    )


def _token(fqn: str = "human/tenant-1/ws-1/alice", amount: float = 10.0) -> ReservationToken:
    return ReservationToken(
        token_id="tok-abc",
        principal_fqn=fqn,
        amount=amount,
        granted_at="2026-01-01T00:00:00+00:00",
        expires_at="2026-01-01T00:01:00+00:00",
    )


class _FakeBalance:
    """Plain-Python stand-in for EconomicBalance (avoids SA instrumentation)."""

    def __init__(
        self,
        fqn: str,
        deposited: float = 100.0,
        committed: float = 0.0,
        reserved: float = 0.0,
    ) -> None:
        self.principal_fqn = fqn
        self.deposited = deposited
        self.committed = committed
        self.reserved = reserved


class _FakeReservation:
    """Plain-Python stand-in for EconomicReservation."""

    def __init__(self, token_id: str, fqn: str, amount: float = 10.0) -> None:
        self.token_id = token_id
        self.principal_fqn = fqn
        self.amount = amount
        self.granted_at = datetime.now(_UTC)
        self.expires_at = datetime(2099, 1, 1, tzinfo=_UTC)


class _FakeSignalRow:
    """Plain-Python stand-in for EconomicSignalRow."""

    def __init__(self, fqn: str, kind: str = "budget_exhausted") -> None:
        self.id = "sig-1"
        self.principal_fqn = fqn
        self.signal_type = kind
        self.value = 1.0
        self.detail = "test"
        self.issued_at = datetime.now(_UTC)


def _make_balance(fqn: str, deposited: float = 100.0, committed: float = 0.0, reserved: float = 0.0) -> _FakeBalance:
    return _FakeBalance(fqn, deposited=deposited, committed=committed, reserved=reserved)


def _make_reservation(token_id: str, fqn: str, amount: float = 10.0) -> _FakeReservation:
    return _FakeReservation(token_id, fqn, amount)


def _make_signal_row(fqn: str, kind: str = "budget_exhausted") -> _FakeSignalRow:
    return _FakeSignalRow(fqn, kind)


class _MockSession:
    """Minimal async session double."""

    def __init__(self) -> None:
        self._objects: dict[tuple[Any, Any], Any] = {}
        self.added: list[Any] = []
        self.deleted: list[Any] = []

    async def get(
        self,
        model: type,
        pk: Any,
        with_for_update: bool = False,
    ) -> Any:
        return self._objects.get((model, pk))

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def delete(self, obj: Any) -> None:
        self.deleted.append(obj)

    async def execute(self, stmt: Any) -> Any:
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        return result

    async def __aenter__(self) -> "_MockSession":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        pass

    def begin(self) -> "_BeginCtx":
        return _BeginCtx()


class _BeginCtx:
    async def __aenter__(self) -> "_BeginCtx":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        pass


def _make_ledger_with_session(session: _MockSession) -> PostgresBudgetLedger:
    """Build a ledger whose session factory always returns the given session."""
    ledger = PostgresBudgetLedger.__new__(PostgresBudgetLedger)
    ledger._database_url = "postgresql+asyncpg://fake/fake"
    ledger._pool_size = 1
    ledger._engine = MagicMock()
    factory = MagicMock()
    factory.return_value = session
    ledger._session_factory = factory
    return ledger


# ===========================================================================
# Protocol conformance
# ===========================================================================


class TestProtocolConformance:
    def test_is_budget_ledger(self) -> None:
        ledger = PostgresBudgetLedger("postgresql+asyncpg://fake/fake")
        assert isinstance(ledger, BudgetLedger)

    def test_is_economic_signal_source(self) -> None:
        ledger = PostgresBudgetLedger("postgresql+asyncpg://fake/fake")
        assert isinstance(ledger, EconomicSignalSource)

    def test_engine_is_none_before_init_db(self) -> None:
        ledger = PostgresBudgetLedger("postgresql+asyncpg://fake/fake")
        assert ledger._engine is None

    async def test_init_db_creates_engine(self) -> None:
        """init_db should set up engine and session factory."""
        with patch(
            "ravi.integrations.economic._postgres_ledger.create_async_engine"
        ) as mock_engine_factory, patch(
            "ravi.integrations.economic._postgres_ledger.async_sessionmaker"
        ) as mock_sm:
            mock_engine = MagicMock()
            mock_conn_ctx = AsyncMock()
            mock_conn_ctx.__aenter__ = AsyncMock(return_value=mock_conn_ctx)
            mock_conn_ctx.__aexit__ = AsyncMock(return_value=None)
            mock_conn_ctx.run_sync = AsyncMock(return_value=None)
            mock_engine.begin.return_value = mock_conn_ctx
            mock_engine_factory.return_value = mock_engine
            mock_sm.return_value = MagicMock()

            ledger = PostgresBudgetLedger("postgresql+asyncpg://fake/fake")
            assert ledger._engine is None

            await ledger.init_db()

            assert ledger._engine is mock_engine
            mock_engine_factory.assert_called_once()


# ===========================================================================
# deposit
# ===========================================================================


class TestDeposit:
    async def test_deposit_creates_balance_if_absent(self) -> None:
        session = _MockSession()
        ledger = _make_ledger_with_session(session)
        principal = _principal()

        await ledger.deposit(principal, 50.0)

        # A new EconomicBalance should have been added
        balances = [o for o in session.added if isinstance(o, EconomicBalance)]
        assert len(balances) == 1
        assert balances[0].deposited == 50.0

    async def test_deposit_increments_existing_balance(self) -> None:
        session = _MockSession()
        fqn = _principal().fqn
        existing = _make_balance(fqn, deposited=100.0)
        session._objects[(EconomicBalance, fqn)] = existing
        ledger = _make_ledger_with_session(session)

        await ledger.deposit(_principal(), 25.0)

        assert existing.deposited == 125.0

    async def test_deposit_negative_raises(self) -> None:
        session = _MockSession()
        ledger = _make_ledger_with_session(session)
        with pytest.raises(ValueError):
            await ledger.deposit(_principal(), -10.0)


# ===========================================================================
# available_for
# ===========================================================================


class TestAvailableFor:
    async def test_available_for_unknown_principal_returns_zero(self) -> None:
        session = _MockSession()
        ledger = _make_ledger_with_session(session)
        result = await ledger.available_for(_principal())
        assert result == 0.0

    async def test_available_for_returns_correct_value(self) -> None:
        session = _MockSession()
        principal = _principal()
        fqn = principal.fqn
        balance = _make_balance(fqn, deposited=100.0, committed=30.0, reserved=10.0)
        session._objects[(EconomicBalance, fqn)] = balance
        ledger = _make_ledger_with_session(session)

        result = await ledger.available_for(principal)

        assert result == pytest.approx(60.0)


# ===========================================================================
# reserve
# ===========================================================================


class TestReserve:
    async def test_reserve_succeeds_when_balance_available(self) -> None:
        session = _MockSession()
        principal = _principal()
        fqn = principal.fqn
        balance = _make_balance(fqn, deposited=100.0, committed=0.0, reserved=0.0)
        session._objects[(EconomicBalance, fqn)] = balance
        ledger = _make_ledger_with_session(session)

        token = await ledger.reserve(principal, 20.0, ttl_seconds=60.0)

        assert token.principal_fqn == fqn
        assert token.amount == 20.0
        # reserved should be incremented
        assert balance.reserved == pytest.approx(20.0)
        # EconomicReservation added to session
        reservations = [o for o in session.added if isinstance(o, EconomicReservation)]
        assert len(reservations) == 1
        assert reservations[0].amount == 20.0

    async def test_reserve_raises_budget_exhausted_when_insufficient(self) -> None:
        session = _MockSession()
        principal = _principal()
        fqn = principal.fqn
        balance = _make_balance(fqn, deposited=5.0, committed=0.0, reserved=0.0)
        session._objects[(EconomicBalance, fqn)] = balance
        ledger = _make_ledger_with_session(session)

        with pytest.raises(BudgetExhausted) as exc_info:
            await ledger.reserve(principal, 10.0)

        assert exc_info.value.requested == 10.0
        # A signal row should have been added
        signal_rows = [o for o in session.added if isinstance(o, EconomicSignalRow)]
        assert len(signal_rows) == 1
        assert signal_rows[0].signal_type == "budget_exhausted"

    async def test_reserve_negative_amount_raises(self) -> None:
        session = _MockSession()
        ledger = _make_ledger_with_session(session)
        with pytest.raises(ValueError):
            await ledger.reserve(_principal(), -1.0)


# ===========================================================================
# commit
# ===========================================================================


class TestCommit:
    async def test_commit_succeeds_on_valid_token(self) -> None:
        session = _MockSession()
        principal = _principal()
        fqn = principal.fqn
        balance = _make_balance(fqn, deposited=100.0, committed=0.0, reserved=20.0)
        reservation = _make_reservation("tok-abc", fqn, amount=20.0)
        session._objects[(EconomicBalance, fqn)] = balance
        session._objects[(EconomicReservation, "tok-abc")] = reservation
        ledger = _make_ledger_with_session(session)

        tok = _token(fqn, 20.0)
        await ledger.commit(tok)

        # reserved decremented, committed incremented
        assert balance.reserved == pytest.approx(0.0)
        assert balance.committed == pytest.approx(20.0)
        # reservation deleted
        assert reservation in session.deleted

    async def test_commit_raises_reservation_lost_on_missing_token(self) -> None:
        session = _MockSession()
        ledger = _make_ledger_with_session(session)

        with pytest.raises(ReservationLost) as exc_info:
            await ledger.commit(_token())

        assert exc_info.value.token_id == "tok-abc"


# ===========================================================================
# release
# ===========================================================================


class TestRelease:
    async def test_release_is_noop_on_missing_reservation(self) -> None:
        session = _MockSession()
        ledger = _make_ledger_with_session(session)
        # Must not raise
        await ledger.release(_token())
        assert session.deleted == []

    async def test_release_decrements_reserved_on_success(self) -> None:
        session = _MockSession()
        principal = _principal()
        fqn = principal.fqn
        balance = _make_balance(fqn, deposited=100.0, committed=0.0, reserved=10.0)
        reservation = _make_reservation("tok-abc", fqn, amount=10.0)
        session._objects[(EconomicBalance, fqn)] = balance
        session._objects[(EconomicReservation, "tok-abc")] = reservation
        ledger = _make_ledger_with_session(session)

        await ledger.release(_token(fqn, 10.0))

        assert balance.reserved == pytest.approx(0.0)
        assert reservation in session.deleted


# ===========================================================================
# signals_for
# ===========================================================================


class TestSignalsFor:
    async def test_signals_for_returns_empty_tuple_when_none(self) -> None:
        session = _MockSession()
        ledger = _make_ledger_with_session(session)
        result = await ledger.signals_for(_principal())
        assert result == ()

    async def test_signals_for_returns_parsed_signals(self) -> None:
        session = _MockSession()
        fqn = _principal().fqn
        row = _make_signal_row(fqn, "budget_exhausted")

        async def mock_execute(stmt: Any) -> Any:
            result = MagicMock()
            result.scalars.return_value.all.return_value = [row]
            return result

        session.execute = mock_execute  # type: ignore[method-assign]
        ledger = _make_ledger_with_session(session)

        signals = await ledger.signals_for(_principal())

        assert len(signals) == 1
        assert signals[0].signal_type == EconomicSignalKind.BUDGET_EXHAUSTED
        assert signals[0].principal_fqn == fqn
