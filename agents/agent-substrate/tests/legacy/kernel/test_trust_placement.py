"""Tests for TrustContext, TrustLevel, PlacementContract, and related kernel contracts."""

from __future__ import annotations

import pytest

from ravi.kernel.contracts._coordination import (
    DataGravityHint,
    PlacementContract,
    PlacementScope,
    TrustContext,
    TrustLevel,
    TrustSignal,
)
from ravi.kernel.runtime._contracts import Envelope
from ravi.kernel.runtime._identity import AgentId


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agent(name: str = "test") -> AgentId:
    return AgentId(type="test_agent", key=name)


def _signal(value: float = 0.8) -> TrustSignal:
    return TrustSignal(
        signal_type="moderation_pass",
        value=value,
        source_id="auth-service",
        issued_at="2026-05-25T00:00:00Z",
    )


# ---------------------------------------------------------------------------
# TrustLevel ordering
# ---------------------------------------------------------------------------


class TestTrustLevelOrdering:
    def test_untrusted_is_lowest(self) -> None:
        assert TrustLevel.UNTRUSTED.value < TrustLevel.LOW.value

    def test_low_lt_medium(self) -> None:
        assert TrustLevel.LOW.value < TrustLevel.MEDIUM.value

    def test_medium_lt_high(self) -> None:
        assert TrustLevel.MEDIUM.value < TrustLevel.HIGH.value

    def test_high_lt_verified(self) -> None:
        assert TrustLevel.HIGH.value < TrustLevel.VERIFIED.value

    def test_full_ordering(self) -> None:
        levels = [TrustLevel.UNTRUSTED, TrustLevel.LOW, TrustLevel.MEDIUM, TrustLevel.HIGH, TrustLevel.VERIFIED]
        values = [lvl.value for lvl in levels]
        assert values == sorted(values)
        assert len(set(values)) == 5  # all unique


# ---------------------------------------------------------------------------
# TrustContext.is_at_least
# ---------------------------------------------------------------------------


class TestTrustContextIsAtLeast:
    def test_medium_is_at_least_low(self) -> None:
        ctx = TrustContext(level=TrustLevel.MEDIUM)
        assert ctx.is_at_least(TrustLevel.LOW)

    def test_medium_is_not_at_least_high(self) -> None:
        ctx = TrustContext(level=TrustLevel.MEDIUM)
        assert not ctx.is_at_least(TrustLevel.HIGH)

    def test_medium_is_at_least_medium(self) -> None:
        ctx = TrustContext(level=TrustLevel.MEDIUM)
        assert ctx.is_at_least(TrustLevel.MEDIUM)

    def test_untrusted_is_not_at_least_low(self) -> None:
        ctx = TrustContext(level=TrustLevel.UNTRUSTED)
        assert not ctx.is_at_least(TrustLevel.LOW)


# ---------------------------------------------------------------------------
# TrustContext.effective_level
# ---------------------------------------------------------------------------


class TestTrustContextEffectiveLevel:
    def test_returns_tenant_override_when_set(self) -> None:
        ctx = TrustContext(level=TrustLevel.LOW, tenant_override=TrustLevel.VERIFIED)
        assert ctx.effective_level == TrustLevel.VERIFIED

    def test_returns_level_when_override_is_none(self) -> None:
        ctx = TrustContext(level=TrustLevel.HIGH, tenant_override=None)
        assert ctx.effective_level == TrustLevel.HIGH

    def test_default_level_is_medium(self) -> None:
        ctx = TrustContext()
        assert ctx.effective_level == TrustLevel.MEDIUM


# ---------------------------------------------------------------------------
# TrustSignal immutability
# ---------------------------------------------------------------------------


class TestTrustSignalImmutability:
    def test_frozen(self) -> None:
        sig = _signal()
        with pytest.raises((AttributeError, TypeError)):
            sig.value = 0.1  # type: ignore[misc]

    def test_slots(self) -> None:
        sig = _signal()
        assert not hasattr(sig, "__dict__")


# ---------------------------------------------------------------------------
# PlacementContract.primary_gravity
# ---------------------------------------------------------------------------


class TestPlacementContractPrimaryGravity:
    def test_returns_none_when_no_gravity(self) -> None:
        pc = PlacementContract()
        assert pc.primary_gravity is None

    def test_returns_first_hint(self) -> None:
        h1 = DataGravityHint(store_uri="postgres://db/t", partition_key="shard-1", byte_estimate=1000)
        h2 = DataGravityHint(store_uri="s3://bucket/prefix", partition_key="p2", byte_estimate=500)
        pc = PlacementContract(data_gravity=(h1, h2))
        assert pc.primary_gravity is h1

    def test_primary_gravity_with_single_hint(self) -> None:
        h = DataGravityHint(store_uri="postgres://db/t", partition_key="k")
        pc = PlacementContract(data_gravity=(h,))
        assert pc.primary_gravity is h


# ---------------------------------------------------------------------------
# PlacementScope completeness
# ---------------------------------------------------------------------------


class TestPlacementScope:
    def test_has_five_values(self) -> None:
        assert len(PlacementScope) == 5

    def test_all_expected_names(self) -> None:
        names = {s.name for s in PlacementScope}
        assert names == {"LOCAL", "SHARD", "REGION", "NEAREST", "ANY"}


# ---------------------------------------------------------------------------
# Envelope integration
# ---------------------------------------------------------------------------


class TestEnvelopeIntegration:
    def test_envelope_stores_trust_and_placement(self) -> None:
        trust = TrustContext(level=TrustLevel.HIGH, score=0.9)
        placement = PlacementContract(scope=PlacementScope.REGION, region="us-east-1")
        env = Envelope(
            sender=_agent("sender"),
            target=_agent("target"),
            content=[],
            trust=trust,
            placement=placement,
        )
        assert env.trust is trust
        assert env.placement is placement

    def test_envelope_trust_defaults_to_none(self) -> None:
        env = Envelope(sender=None, target=_agent(), content=[])
        assert env.trust is None

    def test_envelope_placement_defaults_to_none(self) -> None:
        env = Envelope(sender=None, target=_agent(), content=[])
        assert env.placement is None


# ---------------------------------------------------------------------------
# Top-level import from ravi.kernel
# ---------------------------------------------------------------------------


class TestKernelTopLevelImport:
    def test_import_trust_context_from_kernel(self) -> None:
        from ravi.kernel import TrustContext as TC  # noqa: PLC0415
        assert TC is TrustContext

    def test_import_placement_contract_from_kernel(self) -> None:
        from ravi.kernel import PlacementContract as PC  # noqa: PLC0415
        assert PC is PlacementContract
