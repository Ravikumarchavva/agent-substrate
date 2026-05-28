from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ravi.extensions.safeguards import InMemoryMutationPolicy
from ravi.kernel.safeguards import (
    MutationKind,
    MutationPolicy,
    MutationRequest,
)

UTC = timezone.utc


class MutableClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _request(
    *,
    request_id: str = "req-1",
    principal_fqn: str = "tenant.agent.alice",
    kind: MutationKind = MutationKind.TOOL_ADD,
    family_depth: int = 1,
) -> MutationRequest:
    return MutationRequest(
        request_id=request_id,
        principal_fqn=principal_fqn,
        target_agent_fqn="tenant.agent.target",
        kind=kind,
        family_depth=family_depth,
        payload_summary="attach safe tool",
        requested_at="2026-01-01T12:00:00+00:00",
    )


class TestProtocolConformance:
    async def test_isinstance_mutation_policy(self) -> None:
        assert isinstance(InMemoryMutationPolicy(), MutationPolicy)


class TestMutationGates:
    async def test_weight_update_forbidden_by_default(self) -> None:
        policy = InMemoryMutationPolicy()

        decision = await policy.evaluate(
            _request(kind=MutationKind.WEIGHT_UPDATE)
        )

        assert decision.granted is False
        assert decision.reason == "forbidden_kind"
        assert decision.expires_at is None

    async def test_family_depth_ceiling_denies_descendants(self) -> None:
        policy = InMemoryMutationPolicy(max_family_depth=2)

        decision = await policy.evaluate(_request(family_depth=3))

        assert decision.granted is False
        assert decision.reason == "family_depth_ceiling"

    async def test_grant_sets_decision_and_expiry_fields(self) -> None:
        clock = MutableClock()
        policy = InMemoryMutationPolicy(
            grant_ttl_seconds=45.0,
            clock=clock,
        )

        decision = await policy.evaluate(_request())

        assert decision.granted is True
        assert decision.reason == "granted"
        assert decision.decided_at == "2026-01-01T12:00:00+00:00"
        assert decision.expires_at == "2026-01-01T12:00:45+00:00"


class TestRateLimits:
    async def test_rate_limit_is_per_principal(self) -> None:
        clock = MutableClock()
        policy = InMemoryMutationPolicy(
            rate_limit=2,
            rate_window_seconds=10.0,
            clock=clock,
        )

        first = await policy.evaluate(_request(request_id="req-1"))
        second = await policy.evaluate(_request(request_id="req-2"))
        third = await policy.evaluate(_request(request_id="req-3"))
        other = await policy.evaluate(
            _request(
                request_id="req-4",
                principal_fqn="tenant.agent.bob",
            )
        )

        assert first.granted is True
        assert second.granted is True
        assert third.granted is False
        assert third.reason == "rate_limited"
        assert other.granted is True

    async def test_rate_limit_window_expires(self) -> None:
        clock = MutableClock()
        policy = InMemoryMutationPolicy(
            rate_limit=1,
            rate_window_seconds=10.0,
            clock=clock,
        )

        assert (await policy.evaluate(_request(request_id="req-1"))).granted
        assert not (await policy.evaluate(_request(request_id="req-2"))).granted

        clock.advance(10.1)

        decision = await policy.evaluate(_request(request_id="req-3"))
        assert decision.granted is True
