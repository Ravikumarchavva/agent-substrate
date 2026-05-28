"""Redis-backed BudgetLedger and EconomicSignalSource.

Implements the kernel economic-plane contracts using ``redis.asyncio``.
All atomic operations use Lua scripts so that check-then-act sequences
are serialised at the Redis server level.

Key schema
----------
``econ:dep:{fqn}``      — float string: total deposited
``econ:com:{fqn}``      — float string: total committed (permanent spend)
``econ:rsv:{fqn}``      — float string: running sum of active reservations
``econ:res:{token_id}`` — hash: fqn, amount, expires_iso
``econ:sig:{fqn}``      — list of JSON-encoded EconomicSignal (capped at 64)

Thread-safety
-------------
``_lock`` guards lazy ``_client`` initialisation only; no lock is held
across any ``await``.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import redis.asyncio as aioredis

from ravi.guardrails.economic import (
    BudgetExhausted,
    EconomicSignal,
    EconomicSignalKind,
    ReservationLost,
    ReservationToken,
)
from ravi.kernel.runtime._identity import PrincipalId

__all__ = ["RedisBudgetLedger"]

_UTC = timezone.utc
_SOURCE_ID = "redis_budget_ledger"
_SIG_CAP = 64

# ---------------------------------------------------------------------------
# Lua scripts
# ---------------------------------------------------------------------------

# reserve(KEYS[1..4], ARGV[1..4])
# KEYS: dep_key, com_key, rsv_key, res_key
# ARGV: amount, token_id, fqn, expires_ts_unix (integer seconds)
_LUA_RESERVE = """
local dep = tonumber(redis.call('GET', KEYS[1])) or 0
local com = tonumber(redis.call('GET', KEYS[2])) or 0
local rsv = tonumber(redis.call('GET', KEYS[3])) or 0
local available = dep - com - rsv
local amount = tonumber(ARGV[1])
if available < amount then
    return {0, tostring(available)}
end
redis.call('INCRBYFLOAT', KEYS[3], amount)
redis.call('HSET', KEYS[4], 'fqn', ARGV[3], 'amount', ARGV[1], 'expires_iso', ARGV[4])
redis.call('EXPIREAT', KEYS[4], tonumber(ARGV[5]))
return {1, tostring(available - amount)}
"""

# commit(KEYS[1..3], ARGV[1])
# KEYS: res_key, rsv_key, com_key
# ARGV: expected_token_id (unused; key already scoped)
_LUA_COMMIT = """
local data = redis.call('HGETALL', KEYS[1])
if #data == 0 then
    return 0
end
local amount = nil
for i = 1, #data, 2 do
    if data[i] == 'amount' then amount = tonumber(data[i+1]) end
end
if not amount then return 0 end
redis.call('DEL', KEYS[1])
redis.call('INCRBYFLOAT', KEYS[2], -amount)
redis.call('INCRBYFLOAT', KEYS[3], amount)
return 1
"""

# release(KEYS[1..2])
# KEYS: res_key, rsv_key
_LUA_RELEASE = """
local data = redis.call('HGETALL', KEYS[1])
if #data == 0 then
    return 0
end
local amount = nil
for i = 1, #data, 2 do
    if data[i] == 'amount' then amount = tonumber(data[i+1]) end
end
if not amount then return 0 end
redis.call('DEL', KEYS[1])
redis.call('INCRBYFLOAT', KEYS[2], -amount)
return 1
"""


class RedisBudgetLedger:
    """Redis-backed implementation of :class:`BudgetLedger` and
    :class:`EconomicSignalSource`.

    Parameters
    ----------
    redis_url:
        Redis connection URL (default ``redis://localhost:6379/0``).
    """

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        self._url = redis_url
        self._lock = threading.RLock()
        self._client: aioredis.Redis | None = None  # type: ignore[type-arg]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _redis(self) -> aioredis.Redis:  # type: ignore[type-arg]
        with self._lock:
            if self._client is None:
                self._client = aioredis.from_url(
                    self._url, decode_responses=True
                )
        return self._client

    @staticmethod
    def _dep_key(fqn: str) -> str:
        return f"econ:dep:{fqn}"

    @staticmethod
    def _com_key(fqn: str) -> str:
        return f"econ:com:{fqn}"

    @staticmethod
    def _rsv_key(fqn: str) -> str:
        return f"econ:rsv:{fqn}"

    @staticmethod
    def _res_key(token_id: str) -> str:
        return f"econ:res:{token_id}"

    @staticmethod
    def _sig_key(fqn: str) -> str:
        return f"econ:sig:{fqn}"

    async def _push_signal(
        self,
        fqn: str,
        kind: EconomicSignalKind,
        value: float,
        detail: str,
    ) -> None:
        client = await self._redis()
        signal = EconomicSignal(
            signal_type=kind,
            principal_fqn=fqn,
            value=max(0.0, min(1.0, value)),
            source_id=_SOURCE_ID,
            issued_at=datetime.now(_UTC).isoformat(),
            detail=detail,
        )
        payload = json.dumps({
            "signal_type": kind.value,
            "principal_fqn": fqn,
            "value": signal.value,
            "source_id": _SOURCE_ID,
            "issued_at": signal.issued_at,
            "detail": detail,
        })
        sig_key = self._sig_key(fqn)
        await client.lpush(sig_key, payload)
        await client.ltrim(sig_key, 0, _SIG_CAP - 1)

    # ------------------------------------------------------------------
    # BudgetLedger protocol
    # ------------------------------------------------------------------

    async def reserve(
        self,
        principal: PrincipalId,
        amount: float,
        *,
        ttl_seconds: float = 60.0,
    ) -> ReservationToken:
        if amount < 0:
            raise ValueError("amount must be >= 0")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")

        fqn = principal.fqn
        now = datetime.now(_UTC)
        expires_at = now + timedelta(seconds=ttl_seconds)
        token_id = uuid.uuid4().hex
        expires_unix = int(expires_at.timestamp()) + 1  # round up

        client = await self._redis()
        result: list[Any] = await client.eval(  # type: ignore[call-overload]
            _LUA_RESERVE,
            4,
            self._dep_key(fqn),
            self._com_key(fqn),
            self._rsv_key(fqn),
            self._res_key(token_id),
            str(amount),
            token_id,
            fqn,
            expires_at.isoformat(),
            str(expires_unix),
        )

        ok = int(result[0])
        if not ok:
            available = float(result[1])
            await self._push_signal(
                fqn,
                EconomicSignalKind.BUDGET_EXHAUSTED,
                1.0,
                f"requested={amount} available={available}",
            )
            raise BudgetExhausted(fqn, amount, available)

        return ReservationToken(
            token_id=token_id,
            principal_fqn=fqn,
            amount=amount,
            granted_at=now.isoformat(),
            expires_at=expires_at.isoformat(),
        )

    async def commit(self, token: ReservationToken) -> None:
        client = await self._redis()
        result: int = await client.eval(  # type: ignore[call-overload]
            _LUA_COMMIT,
            3,
            self._res_key(token.token_id),
            self._rsv_key(token.principal_fqn),
            self._com_key(token.principal_fqn),
        )
        if not int(result):
            raise ReservationLost(token.token_id)

    async def release(self, token: ReservationToken) -> None:
        client = await self._redis()
        await client.eval(  # type: ignore[call-overload]
            _LUA_RELEASE,
            2,
            self._res_key(token.token_id),
            self._rsv_key(token.principal_fqn),
        )
        # No-op if reservation missing — safe by design.

    async def available_for(self, principal: PrincipalId) -> float:
        fqn = principal.fqn
        client = await self._redis()
        dep_raw, com_raw, rsv_raw = await client.mget(
            self._dep_key(fqn),
            self._com_key(fqn),
            self._rsv_key(fqn),
        )
        dep = float(dep_raw) if dep_raw is not None else 0.0
        com = float(com_raw) if com_raw is not None else 0.0
        rsv = float(rsv_raw) if rsv_raw is not None else 0.0
        return max(0.0, dep - com - rsv)

    async def deposit(self, principal: PrincipalId, amount: float) -> None:
        if amount < 0:
            raise ValueError("amount must be >= 0")
        client = await self._redis()
        await client.incrbyfloat(self._dep_key(principal.fqn), amount)

    # ------------------------------------------------------------------
    # EconomicSignalSource protocol
    # ------------------------------------------------------------------

    async def signals_for(
        self, principal: PrincipalId
    ) -> tuple[EconomicSignal, ...]:
        client = await self._redis()
        raw_list: list[str] = await client.lrange(
            self._sig_key(principal.fqn), 0, _SIG_CAP - 1
        )
        signals: list[EconomicSignal] = []
        for raw in raw_list:
            try:
                data = json.loads(raw)
                signals.append(
                    EconomicSignal(
                        signal_type=EconomicSignalKind(data["signal_type"]),
                        principal_fqn=data["principal_fqn"],
                        value=float(data["value"]),
                        source_id=data["source_id"],
                        issued_at=data["issued_at"],
                        detail=data.get("detail", ""),
                    )
                )
            except (KeyError, ValueError, json.JSONDecodeError):
                continue
        return tuple(signals)
