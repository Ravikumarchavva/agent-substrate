from __future__ import annotations

from ravi.guardrails.mutation import (
    BreakerSnapshot,
    BreakerState,
    CircuitBreaker,
    CircuitOpen,
    MutationKind,
    MutationPermission,
    MutationPolicy,
    MutationRequest,
)


class _ContractBreaker:
    async def allow_request(self, principal_fqn: str) -> BreakerSnapshot:
        return _snapshot(principal_fqn)

    async def record_success(self, principal_fqn: str) -> BreakerSnapshot:
        return _snapshot(principal_fqn)

    async def record_failure(self, principal_fqn: str) -> BreakerSnapshot:
        return _snapshot(principal_fqn)

    async def reset(self, principal_fqn: str) -> BreakerSnapshot:
        return _snapshot(principal_fqn)

    async def state_for(self, principal_fqn: str) -> BreakerSnapshot:
        return _snapshot(principal_fqn)


class _ContractPolicy:
    async def evaluate(
        self, request: MutationRequest
    ) -> MutationPermission:
        return MutationPermission(
            request_id=request.request_id,
            granted=True,
            reason="granted",
            decided_at=request.requested_at,
            expires_at=None,
        )


def _snapshot(principal_fqn: str) -> BreakerSnapshot:
    return BreakerSnapshot(
        principal_fqn=principal_fqn,
        state=BreakerState.CLOSED,
        failure_count=0,
        success_count=0,
        failure_threshold=1,
        success_threshold=1,
        updated_at="2026-01-01T00:00:00+00:00",
    )


def test_safeguards_package_exports_kernel_contracts() -> None:
    assert MutationKind.WEIGHT_UPDATE.name == "WEIGHT_UPDATE"
    assert BreakerState.CLOSED.name == "CLOSED"
    assert CircuitOpen(
        principal_fqn="tenant.agent",
        state=BreakerState.OPEN,
        reason="circuit_open",
    ).principal_fqn == "tenant.agent"


def test_runtime_checkable_protocols_accept_structural_types() -> None:
    assert isinstance(_ContractPolicy(), MutationPolicy)
    assert isinstance(_ContractBreaker(), CircuitBreaker)
