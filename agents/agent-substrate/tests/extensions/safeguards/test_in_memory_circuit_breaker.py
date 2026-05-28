from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from ravi.extensions.safeguards import InMemoryCircuitBreaker
from ravi.kernel.safeguards import BreakerState, CircuitBreaker, CircuitOpen

UTC = timezone.utc


class MutableClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class TestProtocolConformance:
    async def test_isinstance_circuit_breaker(self) -> None:
        assert isinstance(InMemoryCircuitBreaker(), CircuitBreaker)


class TestCircuitTransitions:
    async def test_repeated_failures_open_circuit(self) -> None:
        breaker = InMemoryCircuitBreaker(failure_threshold=2)
        principal = "tenant.agent.alice"

        one = await breaker.record_failure(principal)
        two = await breaker.record_failure(principal)

        assert one.state is BreakerState.CLOSED
        assert one.failure_count == 1
        assert two.state is BreakerState.OPEN
        assert two.failure_count == 2

        with pytest.raises(CircuitOpen) as exc_info:
            await breaker.allow_request(principal)

        assert exc_info.value.principal_fqn == principal
        assert exc_info.value.state is BreakerState.OPEN
        assert exc_info.value.reason == "circuit_open"

    async def test_half_open_success_recovers_to_closed(self) -> None:
        clock = MutableClock()
        breaker = InMemoryCircuitBreaker(
            failure_threshold=1,
            recovery_timeout_seconds=5.0,
            success_threshold=1,
            clock=clock,
        )
        principal = "tenant.agent.alice"

        opened = await breaker.record_failure(principal)
        assert opened.state is BreakerState.OPEN

        with pytest.raises(CircuitOpen) as exc_info:
            await breaker.allow_request(principal)
        assert exc_info.value.retry_after_seconds == pytest.approx(5.0)

        clock.advance(5.1)
        probe = await breaker.allow_request(principal)
        assert probe.state is BreakerState.HALF_OPEN
        assert probe.retry_after_seconds == 0.0

        recovered = await breaker.record_success(principal)
        assert recovered.state is BreakerState.CLOSED
        assert recovered.failure_count == 0
        assert recovered.opened_at is None

    async def test_half_open_probe_failure_reopens(self) -> None:
        clock = MutableClock()
        breaker = InMemoryCircuitBreaker(
            failure_threshold=1,
            recovery_timeout_seconds=5.0,
            clock=clock,
        )
        principal = "tenant.agent.alice"

        await breaker.record_failure(principal)
        clock.advance(5.1)
        assert (await breaker.allow_request(principal)).state is (
            BreakerState.HALF_OPEN
        )

        reopened = await breaker.record_failure(principal)

        assert reopened.state is BreakerState.OPEN
        with pytest.raises(CircuitOpen):
            await breaker.allow_request(principal)

    async def test_reset_closes_open_circuit(self) -> None:
        breaker = InMemoryCircuitBreaker(failure_threshold=1)
        principal = "tenant.agent.alice"

        await breaker.record_failure(principal)
        reset = await breaker.reset(principal)
        allowed = await breaker.allow_request(principal)

        assert reset.state is BreakerState.CLOSED
        assert allowed.state is BreakerState.CLOSED


class TestConcurrentFailureAccounting:
    async def test_rlock_backed_failure_count_is_not_lost(self) -> None:
        breaker = InMemoryCircuitBreaker(failure_threshold=1_000)
        principal = "tenant.agent.concurrent"

        def record_failure_from_thread() -> None:
            asyncio.run(breaker.record_failure(principal))

        await asyncio.gather(
            *[
                asyncio.to_thread(record_failure_from_thread)
                for _ in range(200)
            ]
        )

        snapshot = await breaker.state_for(principal)

        assert snapshot.state is BreakerState.CLOSED
        assert snapshot.failure_count == 200
