"""Tests for kernel trust and provenance contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ravi.kernel.contracts._trust import (
    DelegationProof,
    ProvenanceChain,
    ProvenanceLink,
    PrincipalTrustContext,
    TrustScore,
)

UTC = timezone.utc


# ---------------------------------------------------------------------------
# TrustScore
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


class TestTrustScore:
    def test_valid_boundary_values(self) -> None:
        TrustScore(value=0.0, source="local", computed_at=_now())
        TrustScore(value=1.0, source="local", computed_at=_now())

    def test_rejects_value_below_zero(self) -> None:
        with pytest.raises(ValueError):
            TrustScore(value=-0.01, source="local", computed_at=_now())

    def test_rejects_value_above_one(self) -> None:
        with pytest.raises(ValueError):
            TrustScore(value=1.001, source="local", computed_at=_now())

    def test_is_stale_fresh(self) -> None:
        score = TrustScore(value=0.9, source="graph", computed_at=_now(), decay_seconds=3600.0)
        assert not score.is_stale

    def test_is_stale_old(self) -> None:
        old_time = _now() - timedelta(seconds=3601)
        score = TrustScore(value=0.9, source="graph", computed_at=old_time, decay_seconds=3600.0)
        assert score.is_stale

    def test_is_stale_custom_decay(self) -> None:
        old_time = _now() - timedelta(seconds=11)
        score = TrustScore(value=0.5, source="local", computed_at=old_time, decay_seconds=10.0)
        assert score.is_stale

    def test_not_stale_within_custom_decay(self) -> None:
        recent = _now() - timedelta(seconds=5)
        score = TrustScore(value=0.5, source="local", computed_at=recent, decay_seconds=10.0)
        assert not score.is_stale


# ---------------------------------------------------------------------------
# DelegationProof
# ---------------------------------------------------------------------------


class TestDelegationProof:
    def _make(self, scope: list[str], expires_at: datetime | None = None) -> DelegationProof:
        return DelegationProof(
            delegator_id="agent:ns/parent",
            delegatee_id="agent:ns/child",
            scope=scope,
            issued_at=_now(),
            expires_at=expires_at,
        )

    def test_covers_exact_match(self) -> None:
        proof = self._make(["read", "write:memory"])
        assert proof.covers("read")
        assert proof.covers("write:memory")

    def test_covers_no_match(self) -> None:
        proof = self._make(["read"])
        assert not proof.covers("write")

    def test_covers_wildcard(self) -> None:
        proof = self._make(["write:*"])
        assert proof.covers("write:memory")
        assert proof.covers("write:anything")
        assert not proof.covers("read:memory")

    def test_covers_wildcard_does_not_match_prefix_only(self) -> None:
        proof = self._make(["write:*"])
        # "write" alone does not start with "write:" so should not match
        assert not proof.covers("write")

    def test_is_valid_no_expiry(self) -> None:
        proof = self._make(["read"])
        assert proof.is_valid

    def test_is_valid_future_expiry(self) -> None:
        proof = self._make(["read"], expires_at=_now() + timedelta(hours=1))
        assert proof.is_valid

    def test_is_valid_past_expiry(self) -> None:
        proof = self._make(["read"], expires_at=_now() - timedelta(seconds=1))
        assert not proof.is_valid


# ---------------------------------------------------------------------------
# PrincipalTrustContext
# ---------------------------------------------------------------------------


class TestTrustContext:
    def test_is_quarantined_false(self) -> None:
        ctx = PrincipalTrustContext(principal_id="agent:ns/x", risk_flags=["rate_limited"])
        assert not ctx.is_quarantined

    def test_is_quarantined_true(self) -> None:
        ctx = PrincipalTrustContext(principal_id="agent:ns/x", risk_flags=["quarantined"])
        assert ctx.is_quarantined

    def test_effective_trust_with_score(self) -> None:
        score = TrustScore(value=0.75, source="reputation", computed_at=_now())
        ctx = PrincipalTrustContext(principal_id="agent:ns/x", trust_score=score)
        assert ctx.effective_trust == pytest.approx(0.75)

    def test_effective_trust_without_score(self) -> None:
        ctx = PrincipalTrustContext(principal_id="agent:ns/x")
        assert ctx.effective_trust == 0.0

    def test_can_delegate_true(self) -> None:
        proof = DelegationProof(
            delegator_id="p", delegatee_id="c",
            scope=["memory:*"], issued_at=_now(),
        )
        ctx = PrincipalTrustContext(principal_id="c", delegations=[proof])
        assert ctx.can_delegate(capability="memory:read")

    def test_can_delegate_false_wrong_scope(self) -> None:
        proof = DelegationProof(
            delegator_id="p", delegatee_id="c",
            scope=["read"], issued_at=_now(),
        )
        ctx = PrincipalTrustContext(principal_id="c", delegations=[proof])
        assert not ctx.can_delegate(capability="write")

    def test_can_delegate_false_expired(self) -> None:
        proof = DelegationProof(
            delegator_id="p", delegatee_id="c",
            scope=["read"], issued_at=_now(),
            expires_at=_now() - timedelta(seconds=1),
        )
        ctx = PrincipalTrustContext(principal_id="c", delegations=[proof])
        assert not ctx.can_delegate(capability="read")

    def test_with_flag_returns_new_instance(self) -> None:
        ctx = PrincipalTrustContext(principal_id="agent:ns/x", risk_flags=["rate_limited"])
        new_ctx = ctx.with_flag("quarantined")
        assert new_ctx is not ctx
        assert "quarantined" in new_ctx.risk_flags
        assert "quarantined" not in ctx.risk_flags
        assert "rate_limited" in new_ctx.risk_flags

    def test_with_flag_delegations_shallow_copy(self) -> None:
        proof = DelegationProof(
            delegator_id="p", delegatee_id="c",
            scope=["read"], issued_at=_now(),
        )
        ctx = PrincipalTrustContext(principal_id="c", delegations=[proof])
        new_ctx = ctx.with_flag("flagged")
        assert new_ctx.delegations == ctx.delegations
        assert new_ctx.delegations is not ctx.delegations


# ---------------------------------------------------------------------------
# ProvenanceChain
# ---------------------------------------------------------------------------


class TestProvenanceChain:
    def _link(self, source_id: str = "agent:ns/a") -> ProvenanceLink:
        return ProvenanceLink(
            source_id=source_id,
            source_kind="agent",
            produced_at=_now(),
        )

    def test_depth_empty(self) -> None:
        chain = ProvenanceChain()
        assert chain.depth == 0

    def test_depth_after_append(self) -> None:
        chain = ProvenanceChain()
        chain.append(self._link())
        chain.append(self._link("agent:ns/b"))
        assert chain.depth == 2

    def test_exceeds_depth_false(self) -> None:
        chain = ProvenanceChain()
        chain.append(self._link())
        assert not chain.exceeds_depth(2)

    def test_exceeds_depth_true(self) -> None:
        chain = ProvenanceChain()
        for i in range(3):
            chain.append(self._link(f"agent:ns/{i}"))
        assert chain.exceeds_depth(2)

    def test_latest_empty(self) -> None:
        chain = ProvenanceChain()
        assert chain.latest is None

    def test_latest_returns_last(self) -> None:
        chain = ProvenanceChain()
        first = self._link("agent:ns/first")
        last = self._link("agent:ns/last")
        chain.append(first)
        chain.append(last)
        assert chain.latest is last

    def test_root_event_id(self) -> None:
        chain = ProvenanceChain(root_event_id="evt-abc-123")
        assert chain.root_event_id == "evt-abc-123"
