"""Tests for the 4 remaining hyperscale items:

S11 — Governance sweep: background task quarantines coalition members;
      scheduler grant is revoked when governance sweep fires.
S14 — Replay route: POST /admin/replay/admit, GET /admission/{key},
      POST /deny, DELETE /deny/{rule_id}.
S15 — Semantic checks: invariants evaluated after dispatch; CRITICAL severity
      routes to quarantine actuator.
S16 — Region-local routing: unavailable local region triggers fallback decision
      log; placement_region mismatch is logged.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ravi.agents.events import InMemoryEventFabric
from ravi.guardrails.governance._in_memory import (
    InMemoryGovernancePolicy,
    InMemoryCoalitionDetector,
    InMemoryQuarantineActuator,
)
from ravi.agents.runtime import DistributedRuntime
from ravi.guardrails.semantic._in_memory import (
    DeterministicSemanticInvariantChecker,
    InMemorySemanticDivergenceDetector,
)
from ravi.kernel.control_plane._contracts import (
    FailoverReason,
    LocalFallbackPolicy,
    RegionSpec,
)
from ravi.kernel.governance._contracts import (
    Coalition,
    CoalitionKind,
    GovernanceAction,
    GovernanceDecision,
    GovernanceEvidence,
    GovernancePolicy,
    RiskScore,
)
from ravi.kernel.observability import ReplayDenyRule, ReplayRequest
from ravi.kernel.runtime import AgentId, InMemoryLeaseRegistry, MessageContext
from ravi.kernel.scheduler._contracts import (
    ResourceClaim,
    SlotGrant,
    SlotGrantStatus,
)
from ravi.kernel.semantics._contracts import (
    SemanticInvariant,
    SemanticInvariantKind,
    SemanticSeverity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _echo_handler(ctx: MessageContext, payload: Any) -> str:
    return f"echo:{payload[0].text}"


def _runtime(**kwargs) -> DistributedRuntime:
    fabric = InMemoryEventFabric()
    registry = InMemoryLeaseRegistry()
    return DistributedRuntime(fabric=fabric, lease_registry=registry, worker_id="W", **kwargs)


# ---------------------------------------------------------------------------
# S11 — Governance sweep
# ---------------------------------------------------------------------------


class TestGovernanceSweep:
    async def test_sweep_quarantines_coalition_member(self) -> None:
        """_do_governance_sweep quarantines members whose risk exceeds threshold."""
        now_iso = "2026-01-01T00:00:00+00:00"
        coalition = Coalition(
            coalition_id="c1",
            member_fqns=("agent:a1", "agent:a2"),
            kind=CoalitionKind.TRUST_INFLATION,
            confidence=0.99,
            detected_at=now_iso,
        )

        # Governance policy that always returns QUARANTINE for the members.
        class _AlwaysQuarantine:
            async def evaluate(self, evidence: GovernanceEvidence) -> GovernanceDecision:
                return GovernanceDecision(
                    principal_fqn=evidence.principal_fqn,
                    action=GovernanceAction.QUARANTINE,
                    rationale="test quarantine",
                    decided_at=now_iso,
                )

            async def score_risk(self, fqn: str) -> RiskScore:
                return RiskScore(
                    principal_fqn=fqn, score=0.95, contributors=("test",), scored_at=now_iso
                )

        class _DetectsOneCoalition:
            async def detect(self):
                return (coalition,)

            async def observe(self, *a, **kw):
                pass

            async def disband(self, cid):
                pass

        actuator = InMemoryQuarantineActuator()
        rt = _runtime(
            governance_policy=_AlwaysQuarantine(),
            coalition_detector=_DetectsOneCoalition(),
            quarantine_actuator=actuator,
        )
        await rt._do_governance_sweep()

        assert await actuator.is_quarantined("agent:a1")
        assert await actuator.is_quarantined("agent:a2")

    async def test_sweep_releases_active_scheduler_grant(self) -> None:
        """Governance sweep revokes the active scheduler slot for a quarantined principal."""
        now_iso = "2026-01-01T00:00:00+00:00"
        coalition = Coalition(
            coalition_id="c2",
            member_fqns=("agent:rogue",),
            kind=CoalitionKind.RESOURCE_FARMING,
            confidence=0.98,
            detected_at=now_iso,
        )

        class _AlwaysQuarantine:
            async def evaluate(self, evidence):
                return GovernanceDecision(
                    principal_fqn=evidence.principal_fqn,
                    action=GovernanceAction.QUARANTINE,
                    rationale="rogue",
                    decided_at=now_iso,
                )

            async def score_risk(self, fqn):
                return RiskScore(principal_fqn=fqn, score=1.0, contributors=(), scored_at=now_iso)

        class _DetectsRogue:
            async def detect(self):
                return (coalition,)

            async def observe(self, *a, **kw):
                pass

            async def disband(self, cid):
                pass

        released: list[str] = []

        class _FakeScheduler:
            async def request_slot(self, claim):
                return SlotGrant(
                    grant_id="g-rogue",
                    principal_fqn=claim.principal_fqn,
                    status=SlotGrantStatus.GRANTED,
                    granted_at=now_iso,
                )

            async def release_slot(self, grant_id):
                released.append(grant_id)

            async def wait_for_slot(self, grant_id):
                pass

            async def check_preemption(self, grant_id):
                return None

            async def capacity(self):
                pass

            async def set_share_weight(self, fqn, weight):
                pass

        actuator = InMemoryQuarantineActuator()
        rt = _runtime(
            governance_policy=_AlwaysQuarantine(),
            coalition_detector=_DetectsRogue(),
            quarantine_actuator=actuator,
            scheduler=_FakeScheduler(),
        )
        # Manually plant a grant entry as if send_message had acquired it.
        rt._active_grants_by_principal["agent:rogue"] = "g-rogue"

        await rt._do_governance_sweep()

        assert await actuator.is_quarantined("agent:rogue")
        assert "g-rogue" in released

    async def test_sweep_skips_without_policy(self) -> None:
        """_do_governance_sweep is a no-op when governance_policy is None."""
        actuator = InMemoryQuarantineActuator()
        rt = _runtime(quarantine_actuator=actuator)
        await rt._do_governance_sweep()  # must not raise

    async def test_governance_sweep_task_started_and_stopped(self) -> None:
        """start() creates the sweep task; stop() cancels it cleanly."""
        now_iso = "2026-01-01T00:00:00+00:00"

        class _AlwaysAllow:
            async def evaluate(self, evidence):
                return GovernanceDecision(
                    principal_fqn=evidence.principal_fqn,
                    action=GovernanceAction.ALLOW,
                    rationale="fine",
                    decided_at=now_iso,
                )

            async def score_risk(self, fqn):
                return RiskScore(principal_fqn=fqn, score=0.0, contributors=(), scored_at=now_iso)

        actuator = InMemoryQuarantineActuator()
        rt = _runtime(
            governance_policy=_AlwaysAllow(),
            quarantine_actuator=actuator,
            governance_sweep_interval=3600.0,  # long interval — won't fire in test
        )
        await rt.register("echo", _echo_handler)
        await rt.start()
        assert rt._governance_sweep_task is not None
        assert not rt._governance_sweep_task.done()
        await rt.stop()
        assert rt._governance_sweep_task is None


# ---------------------------------------------------------------------------
# S15 — Semantic invariant checks
# ---------------------------------------------------------------------------


class TestSemanticChecks:
    def _field_exists_invariant(
        self, field_path: str = "sender", severity=SemanticSeverity.ERROR
    ) -> SemanticInvariant:
        return SemanticInvariant(
            invariant_id="inv-sender-exists",
            kind=SemanticInvariantKind.FIELD_EXISTS,
            field_path=field_path,
            severity=severity,
        )

    async def test_register_invariant_stored(self) -> None:
        rt = _runtime()
        inv = self._field_exists_invariant()
        rt.register_invariant(inv)
        assert inv in rt._semantic_invariants

    async def test_no_invariants_skip_check(self) -> None:
        """_check_semantics is skipped when no invariants are registered."""
        checker = DeterministicSemanticInvariantChecker()
        rt = _runtime(semantic_checker=checker)
        # No invariants — call must not raise
        await rt._check_semantics("ok", AgentId("a", "1"), AgentId("a", "2"))

    async def test_passing_invariant_does_not_quarantine(self) -> None:
        """A passing invariant leaves the sender un-quarantined."""
        checker = DeterministicSemanticInvariantChecker()
        actuator = InMemoryQuarantineActuator()
        rt = _runtime(semantic_checker=checker, quarantine_actuator=actuator)
        # sender field will exist in the event dict
        rt.register_invariant(self._field_exists_invariant("sender"))
        await rt._check_semantics("ok", AgentId("a", "1"), AgentId("a", "2"))
        assert not await actuator.is_quarantined("AgentId(type='a', key='1')")

    async def test_critical_divergence_quarantines_sender(self) -> None:
        """CRITICAL-severity invariant failure routes to quarantine actuator."""
        checker = DeterministicSemanticInvariantChecker()
        actuator = InMemoryQuarantineActuator()
        rt = _runtime(semantic_checker=checker, quarantine_actuator=actuator)
        # Invariant requires field "nonexistent" — will always fail.
        rt.register_invariant(
            SemanticInvariant(
                invariant_id="crit-inv",
                kind=SemanticInvariantKind.FIELD_EXISTS,
                field_path="nonexistent",
                severity=SemanticSeverity.CRITICAL,
            )
        )
        sender = AgentId("agent", "bad-actor")
        await rt._check_semantics("result", sender, AgentId("agent", "target"))
        assert await actuator.is_quarantined(str(sender))

    async def test_error_divergence_does_not_quarantine(self) -> None:
        """ERROR-severity failure logs but does not quarantine."""
        checker = DeterministicSemanticInvariantChecker()
        actuator = InMemoryQuarantineActuator()
        rt = _runtime(semantic_checker=checker, quarantine_actuator=actuator)
        rt.register_invariant(
            SemanticInvariant(
                invariant_id="err-inv",
                kind=SemanticInvariantKind.FIELD_EXISTS,
                field_path="nonexistent",
                severity=SemanticSeverity.ERROR,
            )
        )
        sender = AgentId("agent", "noisy")
        await rt._check_semantics("result", sender, AgentId("agent", "target"))
        assert not await actuator.is_quarantined(str(sender))

    async def test_divergence_detector_used_when_present(self) -> None:
        """When divergence_detector is set it is called instead of bare checker."""
        checker = DeterministicSemanticInvariantChecker()
        detector = InMemorySemanticDivergenceDetector(checker=checker)
        rt = _runtime(divergence_detector=detector)
        rt.register_invariant(self._field_exists_invariant("sender"))
        await rt._check_semantics("ok", AgentId("a", "1"), AgentId("a", "2"))
        # No divergences for "sender" field which is present — history stays empty.
        assert detector.recent_divergences() == ()


# ---------------------------------------------------------------------------
# S16 — Region-local routing
# ---------------------------------------------------------------------------


class _FakeRegionRegistry:
    def __init__(self, local_region: RegionSpec, regions: list[RegionSpec] | None = None):
        self._local = local_region
        self._regions = regions or [local_region]

    def list_regions(self):
        return self._regions

    def get_region(self, region_id: str) -> RegionSpec:
        for r in self._regions:
            if r.region_id == region_id:
                return r
        raise KeyError(region_id)

    def local_region(self) -> RegionSpec:
        return self._local

    def mark_unavailable(self, region_id: str) -> None:
        pass

    def mark_available(self, region_id: str) -> None:
        pass


class _TrackingFallbackPolicy:
    """Fallback policy that records how many times decide_fallback was called."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def decide_fallback(
        self,
        unavailable_region_id: str,
        available_regions,
        reason: FailoverReason,
    ):
        from ravi.kernel.control_plane._contracts import FailoverDecision

        fallback_id = available_regions[0].region_id
        self.calls.append((unavailable_region_id, fallback_id))
        return FailoverDecision(
            original_region_id=unavailable_region_id,
            fallback_region_id=fallback_id,
            reason=reason,
            decided_at="2026-01-01T00:00:00+00:00",
        )


class TestRegionLocalRouting:
    async def test_unavailable_region_invokes_fallback_policy(self) -> None:
        """When local region is unavailable, fallback_policy.decide_fallback is called."""
        local = RegionSpec(region_id="us-east", latency_ms=1.0, is_local=True, available=False)
        eu = RegionSpec(region_id="eu-west", latency_ms=50.0, is_local=False, available=True)
        registry = _FakeRegionRegistry(local, [local, eu])
        policy = _TrackingFallbackPolicy()

        rt = _runtime(region_registry=registry, fallback_policy=policy)
        await rt.register("echo", _echo_handler)
        await rt.send_message("hi", recipient=AgentId("echo", "k1"))

        assert len(policy.calls) == 1
        assert policy.calls[0] == ("us-east", "eu-west")
        await rt.stop()

    async def test_available_region_no_fallback_call(self) -> None:
        """Available local region does not invoke fallback policy."""
        local = RegionSpec(region_id="us-east", latency_ms=1.0, is_local=True, available=True)
        registry = _FakeRegionRegistry(local)
        policy = _TrackingFallbackPolicy()

        rt = _runtime(region_registry=registry, fallback_policy=policy)
        await rt.register("echo", _echo_handler)
        await rt.send_message("hi", recipient=AgentId("echo", "k2"))

        assert len(policy.calls) == 0
        await rt.stop()

    async def test_no_region_registry_no_error(self) -> None:
        """send_message works normally when no region_registry is configured."""
        rt = _runtime()
        await rt.register("echo", _echo_handler)
        result = await rt.send_message("hi", recipient=AgentId("echo", "k3"))
        assert result == "echo:hi"
        await rt.stop()


# ---------------------------------------------------------------------------
# S14 — ReplayGate (unit-level, no HTTP layer needed)
# ---------------------------------------------------------------------------


class TestReplayGate:
    async def test_admit_allows_new_request(self) -> None:
        from ravi.platform.observability._in_memory import InMemoryReplayGate
        from ravi.kernel.observability import ReplayAdmissionStatus

        gate = InMemoryReplayGate()
        req = ReplayRequest(
            envelope_id="env-1",
            correlation_id="cor-1",
            requested_by="admin",
            reason="testing",
        )
        admission = await gate.admit(req)
        assert admission.allowed is True
        assert admission.status == ReplayAdmissionStatus.ALLOWED
        assert admission.replay_token is not None

    async def test_repeat_key_returns_duplicate(self) -> None:
        from ravi.platform.observability._in_memory import InMemoryReplayGate
        from ravi.kernel.observability import ReplayAdmissionStatus

        gate = InMemoryReplayGate()
        req = ReplayRequest(
            envelope_id="env-2",
            correlation_id="cor-2",
            requested_by="admin",
            reason="dup test",
            idempotency_key="fixed-key",
        )
        first = await gate.admit(req)
        second = await gate.admit(req)
        assert second.status == ReplayAdmissionStatus.DUPLICATE
        assert second.replay_token == first.replay_token

    async def test_deny_rule_blocks_matching_envelope(self) -> None:
        from ravi.platform.observability._in_memory import InMemoryReplayGate
        from ravi.kernel.observability import ReplayAdmissionStatus

        gate = InMemoryReplayGate()
        rule = ReplayDenyRule(
            reason="blocked by operator",
            created_by="admin",
            envelope_id="blocked-env",
        )
        await gate.deny(rule)
        req = ReplayRequest(
            envelope_id="blocked-env",
            correlation_id="cor-3",
            requested_by="user",
            reason="retry",
        )
        admission = await gate.admit(req)
        assert admission.allowed is False
        assert admission.status == ReplayAdmissionStatus.DENIED

    async def test_clear_denial_removes_rule(self) -> None:
        from ravi.platform.observability._in_memory import InMemoryReplayGate
        from ravi.kernel.observability import ReplayAdmissionStatus

        gate = InMemoryReplayGate()
        rule = ReplayDenyRule(
            reason="temporary block",
            created_by="admin",
            envelope_id="temp-env",
        )
        await gate.deny(rule)
        removed = await gate.clear_denial(rule.rule_id)
        assert removed is True
        req = ReplayRequest(
            envelope_id="temp-env",
            correlation_id="cor-4",
            requested_by="user",
            reason="retry after lift",
        )
        admission = await gate.admit(req)
        assert admission.allowed is True
        assert admission.status == ReplayAdmissionStatus.ALLOWED

    async def test_admission_for_returns_prior_decision(self) -> None:
        from ravi.platform.observability._in_memory import InMemoryReplayGate

        gate = InMemoryReplayGate()
        req = ReplayRequest(
            envelope_id="env-5",
            correlation_id="cor-5",
            requested_by="user",
            reason="lookup test",
            idempotency_key="lookup-key",
        )
        original = await gate.admit(req)
        found = await gate.admission_for("lookup-key")
        assert found is not None
        assert found.replay_token == original.replay_token

    async def test_admission_for_unknown_key_returns_none(self) -> None:
        from ravi.platform.observability._in_memory import InMemoryReplayGate

        gate = InMemoryReplayGate()
        result = await gate.admission_for("no-such-key")
        assert result is None
