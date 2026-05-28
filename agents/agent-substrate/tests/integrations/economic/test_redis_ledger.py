"""Tests for RedisBudgetLedger.

All Redis I/O is mocked — no live Redis server required.
Follows the pattern from tests/integrations/events/test_redis_backends.py.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from ravi.integrations.economic import RedisBudgetLedger
from ravi.kernel.economic import (
    BudgetExhausted,
    BudgetLedger,
    EconomicSignalSource,
    ReservationLost,
    ReservationToken,
)
from ravi.kernel.runtime._identity import PrincipalId, PrincipalKind


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


def _make_ledger(mock_client: AsyncMock) -> RedisBudgetLedger:
    ledger = RedisBudgetLedger(redis_url="redis://localhost:6379/0")
    ledger._client = mock_client
    return ledger


def _eval_reserve_ok(available_after: float = 90.0) -> list[Any]:
    return [1, str(available_after)]


def _eval_reserve_fail(available: float = 0.0) -> list[Any]:
    return [0, str(available)]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client() -> AsyncMock:
    return AsyncMock()


# ===========================================================================
# Protocol conformance
# ===========================================================================


class TestProtocolConformance:
    def test_is_budget_ledger(self) -> None:
        ledger = RedisBudgetLedger()
        assert isinstance(ledger, BudgetLedger)

    def test_is_economic_signal_source(self) -> None:
        ledger = RedisBudgetLedger()
        assert isinstance(ledger, EconomicSignalSource)

    def test_client_is_none_before_first_use(self) -> None:
        ledger = RedisBudgetLedger(redis_url="redis://test:6379/0")
        assert ledger._client is None

    async def test_client_created_lazily(self) -> None:
        with patch(
            "ravi.integrations.economic._redis_ledger.aioredis"
        ) as mock_aioredis:
            mock_c = AsyncMock()
            mock_c.incrbyfloat = AsyncMock(return_value=100.0)
            mock_aioredis.from_url.return_value = mock_c

            ledger = RedisBudgetLedger(redis_url="redis://test:6379/0")
            assert ledger._client is None
            await ledger.deposit(_principal(), 100.0)

            mock_aioredis.from_url.assert_called_once_with(
                "redis://test:6379/0", decode_responses=True
            )
            assert ledger._client is mock_c


# ===========================================================================
# deposit
# ===========================================================================


class TestDeposit:
    async def test_deposit_calls_incrbyfloat(self, mock_client: AsyncMock) -> None:
        mock_client.incrbyfloat = AsyncMock(return_value=100.0)
        ledger = _make_ledger(mock_client)
        principal = _principal()

        await ledger.deposit(principal, 100.0)

        mock_client.incrbyfloat.assert_called_once_with(
            f"econ:dep:{principal.fqn}", 100.0
        )

    async def test_deposit_increases_available_balance(
        self, mock_client: AsyncMock
    ) -> None:
        """After deposit, available_for should return deposited amount."""
        principal = _principal()
        fqn = principal.fqn
        mock_client.incrbyfloat = AsyncMock(return_value=50.0)
        # mget: dep=50, com=None, rsv=None
        mock_client.mget = AsyncMock(return_value=["50.0", None, None])
        ledger = _make_ledger(mock_client)

        await ledger.deposit(principal, 50.0)
        available = await ledger.available_for(principal)

        assert available == 50.0
        mock_client.mget.assert_called_once_with(
            f"econ:dep:{fqn}", f"econ:com:{fqn}", f"econ:rsv:{fqn}"
        )

    async def test_deposit_negative_raises(self, mock_client: AsyncMock) -> None:
        ledger = _make_ledger(mock_client)
        with pytest.raises(ValueError):
            await ledger.deposit(_principal(), -1.0)


# ===========================================================================
# available_for
# ===========================================================================


class TestAvailableFor:
    async def test_available_for_unknown_principal_returns_zero(
        self, mock_client: AsyncMock
    ) -> None:
        mock_client.mget = AsyncMock(return_value=[None, None, None])
        ledger = _make_ledger(mock_client)
        result = await ledger.available_for(_principal())
        assert result == 0.0

    async def test_available_for_returns_deposited_minus_committed_minus_reserved(
        self, mock_client: AsyncMock
    ) -> None:
        mock_client.mget = AsyncMock(return_value=["100.0", "20.0", "10.0"])
        ledger = _make_ledger(mock_client)
        result = await ledger.available_for(_principal())
        assert result == pytest.approx(70.0)

    async def test_available_for_clamps_to_zero(self, mock_client: AsyncMock) -> None:
        mock_client.mget = AsyncMock(return_value=["10.0", "80.0", "50.0"])
        ledger = _make_ledger(mock_client)
        result = await ledger.available_for(_principal())
        assert result == 0.0


# ===========================================================================
# reserve
# ===========================================================================


class TestReserve:
    async def test_reserve_succeeds_when_balance_available(
        self, mock_client: AsyncMock
    ) -> None:
        mock_client.eval = AsyncMock(return_value=_eval_reserve_ok(90.0))
        mock_client.lpush = AsyncMock(return_value=1)
        mock_client.ltrim = AsyncMock(return_value=True)
        ledger = _make_ledger(mock_client)
        principal = _principal()

        token = await ledger.reserve(principal, 10.0, ttl_seconds=60.0)

        assert token.principal_fqn == principal.fqn
        assert token.amount == 10.0
        assert len(token.token_id) == 32  # uuid4 hex
        mock_client.eval.assert_called_once()

    async def test_reserve_raises_budget_exhausted_when_no_balance(
        self, mock_client: AsyncMock
    ) -> None:
        mock_client.eval = AsyncMock(return_value=_eval_reserve_fail(0.0))
        mock_client.lpush = AsyncMock(return_value=1)
        mock_client.ltrim = AsyncMock(return_value=True)
        ledger = _make_ledger(mock_client)

        with pytest.raises(BudgetExhausted) as exc_info:
            await ledger.reserve(_principal(), 10.0)

        assert exc_info.value.requested == 10.0
        assert exc_info.value.available == 0.0

    async def test_reserve_pushes_signal_on_exhaustion(
        self, mock_client: AsyncMock
    ) -> None:
        mock_client.eval = AsyncMock(return_value=_eval_reserve_fail(0.0))
        mock_client.lpush = AsyncMock(return_value=1)
        mock_client.ltrim = AsyncMock(return_value=True)
        ledger = _make_ledger(mock_client)

        with pytest.raises(BudgetExhausted):
            await ledger.reserve(_principal(), 5.0)

        mock_client.lpush.assert_called_once()
        pushed_payload = json.loads(mock_client.lpush.call_args[0][1])
        assert pushed_payload["signal_type"] == "budget_exhausted"

    async def test_reserve_negative_amount_raises(self, mock_client: AsyncMock) -> None:
        ledger = _make_ledger(mock_client)
        with pytest.raises(ValueError):
            await ledger.reserve(_principal(), -5.0)

    async def test_reserve_zero_ttl_raises(self, mock_client: AsyncMock) -> None:
        ledger = _make_ledger(mock_client)
        with pytest.raises(ValueError):
            await ledger.reserve(_principal(), 1.0, ttl_seconds=0)

    async def test_double_reserve_beyond_balance_fails_second(
        self, mock_client: AsyncMock
    ) -> None:
        """Lua returns ok on first call, fail on second (balance exhausted)."""
        call_count = 0

        async def eval_side_effect(*args: object, **kwargs: object) -> list[Any]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _eval_reserve_ok(0.0)  # first: ok, 0 remaining
            return _eval_reserve_fail(0.0)  # second: fail

        mock_client.eval = eval_side_effect
        mock_client.lpush = AsyncMock(return_value=1)
        mock_client.ltrim = AsyncMock(return_value=True)
        ledger = _make_ledger(mock_client)
        principal = _principal()

        token = await ledger.reserve(principal, 100.0, ttl_seconds=60.0)
        assert token.amount == 100.0

        with pytest.raises(BudgetExhausted):
            await ledger.reserve(principal, 1.0, ttl_seconds=60.0)


# ===========================================================================
# commit
# ===========================================================================


class TestCommit:
    async def test_commit_succeeds_on_valid_token(self, mock_client: AsyncMock) -> None:
        mock_client.eval = AsyncMock(return_value=1)
        ledger = _make_ledger(mock_client)
        token = _token()

        await ledger.commit(token)  # must not raise

        mock_client.eval.assert_called_once()

    async def test_commit_raises_reservation_lost_on_bad_token(
        self, mock_client: AsyncMock
    ) -> None:
        mock_client.eval = AsyncMock(return_value=0)
        ledger = _make_ledger(mock_client)

        with pytest.raises(ReservationLost) as exc_info:
            await ledger.commit(_token())

        assert exc_info.value.token_id == "tok-abc"


# ===========================================================================
# release
# ===========================================================================


class TestRelease:
    async def test_release_is_noop_on_unknown_token(
        self, mock_client: AsyncMock
    ) -> None:
        mock_client.eval = AsyncMock(return_value=0)
        ledger = _make_ledger(mock_client)
        # Must not raise
        await ledger.release(_token())
        mock_client.eval.assert_called_once()

    async def test_release_calls_lua_release_script(
        self, mock_client: AsyncMock
    ) -> None:
        mock_client.eval = AsyncMock(return_value=1)
        ledger = _make_ledger(mock_client)
        token = _token()

        await ledger.release(token)

        mock_client.eval.assert_called_once()
        # First two KEYS are res_key and rsv_key
        call_args = mock_client.eval.call_args[0]
        assert call_args[1] == 2  # numkeys=2


# ===========================================================================
# signals_for
# ===========================================================================


class TestSignalsFor:
    async def test_signals_for_returns_empty_tuple_when_none(
        self, mock_client: AsyncMock
    ) -> None:
        mock_client.lrange = AsyncMock(return_value=[])
        ledger = _make_ledger(mock_client)
        result = await ledger.signals_for(_principal())
        assert result == ()

    async def test_signals_for_parses_json_list(
        self, mock_client: AsyncMock
    ) -> None:
        payload = json.dumps({
            "signal_type": "budget_exhausted",
            "principal_fqn": "human/t/w/alice",
            "value": 1.0,
            "source_id": "redis_budget_ledger",
            "issued_at": "2026-01-01T00:00:00+00:00",
            "detail": "requested=10 available=0",
        })
        mock_client.lrange = AsyncMock(return_value=[payload])
        ledger = _make_ledger(mock_client)

        signals = await ledger.signals_for(_principal())

        assert len(signals) == 1
        assert signals[0].principal_fqn == "human/t/w/alice"
        from ravi.kernel.economic import EconomicSignalKind

        assert signals[0].signal_type == EconomicSignalKind.BUDGET_EXHAUSTED

    async def test_signals_for_skips_corrupt_entries(
        self, mock_client: AsyncMock
    ) -> None:
        mock_client.lrange = AsyncMock(return_value=["not-json", '{"bad": true}'])
        ledger = _make_ledger(mock_client)
        # Must not raise — corrupt entries are skipped
        result = await ledger.signals_for(_principal())
        assert isinstance(result, tuple)
