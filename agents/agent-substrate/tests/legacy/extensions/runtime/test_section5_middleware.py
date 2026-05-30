"""Section 5 regression tests — routing middleware enforcement.

Each test pins one safety invariant the runtime needs at the trust
boundary:

- ``IdentityRequiredMiddleware``: anonymous envelopes are rejected
- ``TenantIsolationMiddleware``: cross-tenant addressing is rejected
- ``DepthLimitMiddleware``: recursive activation past max_depth is rejected
- ``TrustDecayMiddleware``: per-hop decay; quarantine below threshold

Plus a composition test that runs the full chain on a real ``LocalRuntime``
and verifies a malicious cross-tenant call is blocked end-to-end.
"""

from __future__ import annotations

from typing import Any

import pytest

from ravi.agents.runtime import (
    DepthLimitMiddleware,
    IdentityRequiredMiddleware,
    TenantIsolationMiddleware,
    TrustDecayMiddleware,
)
from ravi.kernel.contracts._coordination import TrustContext, TrustLevel
from ravi.kernel.messages.content import TextBlock
from ravi.kernel.runtime import (
    ActivationTrigger,
    AgentActivationContract,
    AgentId,
    AgentLifecycleState,
    Envelope,
    IdentityContext,
    MessageContext,
    PrincipalId,
    PrincipalKind,
    RoutingMiddlewareRejection
)
from ravi.agents.runtime.local import LocalRuntime


def _identity(tenant: str = "t1", name: str = "alice") -> IdentityContext:
    return IdentityContext(
        principal=PrincipalId(
            kind=PrincipalKind.AGENT,
            tenant_id=tenant,
            workspace_id="w1",
            name=name,
        )
    )


def _envelope(
    *,
    target: AgentId | None = None,
    identity: IdentityContext | None = None,
    trust: TrustContext | None = None,
    activation: AgentActivationContract | None = None,
) -> Envelope:
    return Envelope(
        sender=None,
        target=target or AgentId("echo", "1"),
        content=[TextBlock(text="x")],
        identity=identity,
        trust=trust,
        activation=activation,
    )


# ---------------------------------------------------------------------------
# IdentityRequiredMiddleware
# ---------------------------------------------------------------------------


class TestIdentityRequired:
    async def test_rejects_anonymous_envelope(self) -> None:
        mw = IdentityRequiredMiddleware()
        env = _envelope(identity=None)
        with pytest.raises(RoutingMiddlewareRejection) as exc:
            await mw(env)
        assert exc.value.policy_name == "identity_required"

    async def test_accepts_envelope_with_identity(self) -> None:
        mw = IdentityRequiredMiddleware()
        env = _envelope(identity=_identity())
        await mw(env)  # no exception

    async def test_internal_sender_type_exempt(self) -> None:
        mw = IdentityRequiredMiddleware(
            allow_internal_sender_types=("system",)
        )
        env = Envelope(
            sender=AgentId("system", "internal"),
            target=AgentId("echo", "1"),
            content=[TextBlock(text="x")],
        )
        await mw(env)  # passes despite no identity


# ---------------------------------------------------------------------------
# TenantIsolationMiddleware
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    async def test_rejects_when_identity_missing(self) -> None:
        mw = TenantIsolationMiddleware()
        env = _envelope(identity=None, target=AgentId("echo", "t1:agent-1"))
        with pytest.raises(RoutingMiddlewareRejection) as exc:
            await mw(env)
        assert "no IdentityContext" in exc.value.reason

    async def test_rejects_cross_tenant_via_key_prefix(self) -> None:
        mw = TenantIsolationMiddleware()
        env = _envelope(
            identity=_identity(tenant="t1"),
            target=AgentId("echo", "t2:agent-1"),
        )
        with pytest.raises(RoutingMiddlewareRejection) as exc:
            await mw(env)
        assert "'t1'" in exc.value.reason and "'t2'" in exc.value.reason

    async def test_accepts_same_tenant_via_key_prefix(self) -> None:
        mw = TenantIsolationMiddleware()
        env = _envelope(
            identity=_identity(tenant="t1"),
            target=AgentId("echo", "t1:agent-1"),
        )
        await mw(env)

    async def test_uses_resolver_when_provided(self) -> None:
        # Resolver maps target_key → tenant via an external lookup table.
        table = {"agent-1": "t1", "agent-2": "t2"}
        mw = TenantIsolationMiddleware(
            tenant_resolver=lambda aid: table.get(aid.key)
        )
        env_ok = _envelope(
            identity=_identity(tenant="t1"),
            target=AgentId("echo", "agent-1"),
        )
        await mw(env_ok)

        env_bad = _envelope(
            identity=_identity(tenant="t1"),
            target=AgentId("echo", "agent-2"),
        )
        with pytest.raises(RoutingMiddlewareRejection):
            await mw(env_bad)


# ---------------------------------------------------------------------------
# DepthLimitMiddleware
# ---------------------------------------------------------------------------


class TestDepthLimit:
    async def test_no_activation_contract_passes(self) -> None:
        mw = DepthLimitMiddleware()
        env = _envelope(activation=None)
        await mw(env)

    async def test_rejects_at_max_depth(self) -> None:
        mw = DepthLimitMiddleware()
        contract = AgentActivationContract(
            lifecycle_state=AgentLifecycleState.ACTIVE,
            trigger=ActivationTrigger(trigger_type="message", source_id="x"),
            depth=32,
            max_depth=32,
        )
        env = _envelope(activation=contract)
        with pytest.raises(RoutingMiddlewareRejection) as exc:
            await mw(env)
        assert "depth 32" in exc.value.reason

    async def test_hard_ceiling_wins_over_contract_max(self) -> None:
        mw = DepthLimitMiddleware(hard_ceiling=10)
        contract = AgentActivationContract(
            lifecycle_state=AgentLifecycleState.ACTIVE,
            trigger=ActivationTrigger(trigger_type="message", source_id="x"),
            depth=10,
            max_depth=100,
        )
        env = _envelope(activation=contract)
        with pytest.raises(RoutingMiddlewareRejection):
            await mw(env)

    async def test_passes_under_limit(self) -> None:
        mw = DepthLimitMiddleware()
        contract = AgentActivationContract(
            lifecycle_state=AgentLifecycleState.ACTIVE,
            trigger=ActivationTrigger(trigger_type="message", source_id="x"),
            depth=31,
            max_depth=32,
        )
        env = _envelope(activation=contract)
        await mw(env)


# ---------------------------------------------------------------------------
# TrustDecayMiddleware
# ---------------------------------------------------------------------------


class TestTrustDecay:
    async def test_no_trust_passes_untouched(self) -> None:
        mw = TrustDecayMiddleware()
        env = _envelope(trust=None)
        await mw(env)
        assert env.trust is None

    async def test_decays_score_per_hop(self) -> None:
        mw = TrustDecayMiddleware(decay_factor=0.5, quarantine_threshold=0.0)
        env = _envelope(trust=TrustContext(score=0.8, level=TrustLevel.HIGH))
        await mw(env)
        assert env.trust is not None
        assert env.trust.score == pytest.approx(0.4)

    async def test_relevels_after_decay(self) -> None:
        mw = TrustDecayMiddleware(decay_factor=0.5, quarantine_threshold=0.0)
        env = _envelope(trust=TrustContext(score=0.95, level=TrustLevel.VERIFIED))
        await mw(env)
        assert env.trust is not None
        # 0.95 * 0.5 = 0.475 → MEDIUM (>=0.4)
        assert env.trust.level is TrustLevel.MEDIUM

    async def test_drops_below_quarantine_threshold(self) -> None:
        from ravi.kernel.runtime._middleware import DropEnvelope

        mw = TrustDecayMiddleware(decay_factor=0.5, quarantine_threshold=0.5)
        env = _envelope(trust=TrustContext(score=0.4, level=TrustLevel.MEDIUM))
        with pytest.raises(DropEnvelope) as exc:
            await mw(env)
        assert "below quarantine" in exc.value.reason

    async def test_invalid_decay_factor_rejected(self) -> None:
        with pytest.raises(ValueError):
            TrustDecayMiddleware(decay_factor=0.0)
        with pytest.raises(ValueError):
            TrustDecayMiddleware(decay_factor=1.5)


# ---------------------------------------------------------------------------
# End-to-end composition on a real runtime
# ---------------------------------------------------------------------------


class TestComposedChainOnRuntime:
    async def test_cross_tenant_call_blocked_end_to_end(self) -> None:
        rt = LocalRuntime(
            routing_middleware=[
                IdentityRequiredMiddleware(),
                TenantIsolationMiddleware(),
            ]
        )

        async def handler(ctx: MessageContext, payload: Any) -> str:
            return "served"

        await rt.register("echo", handler)
        await rt._ensure_started()

        # Same-tenant call works.
        # We need an envelope with identity; LocalRuntime.send_message doesn't
        # set identity itself. Build the envelope directly and dispatch via
        # the lower-level path. For end-to-end ergonomics, we craft a wrapper
        # that injects identity into the envelope before dispatch.
        original_send = rt.send_message

        async def send_as(identity, *, recipient, message):
            # Mimic what an HTTP boundary middleware would do: enrich
            # the outgoing envelope with the verified identity. We do it
            # by overriding the routing middleware temporarily to attach
            # identity at the front of the chain.
            class _Attach:
                name = "_test_attach"

                async def __call__(self, env):
                    env.identity = identity
                    # Re-derive tenancy from the just-attached identity.
                    if env.tenant_id == "default":
                        env.tenant_id = identity.effective_tenant_id

            rt._routing_middleware.insert(0, _Attach())
            try:
                return await original_send(message, recipient=recipient)
            finally:
                rt._routing_middleware.pop(0)

        # Same tenant — passes.
        result = await send_as(
            _identity(tenant="t1"),
            recipient=AgentId("echo", "t1:agent-1"),
            message="hi",
        )
        assert result == "served"

        # Cross tenant — rejected by TenantIsolationMiddleware.
        with pytest.raises(RoutingMiddlewareRejection) as exc:
            await send_as(
                _identity(tenant="t1"),
                recipient=AgentId("echo", "t2:agent-2"),
                message="hi",
            )
        assert exc.value.policy_name == "tenant_isolation"

        await rt.stop()

    async def test_anonymous_send_rejected(self) -> None:
        rt = LocalRuntime(routing_middleware=[IdentityRequiredMiddleware()])

        async def handler(ctx: MessageContext, payload: Any) -> str:
            return "served"

        await rt.register("echo", handler)

        with pytest.raises(RoutingMiddlewareRejection) as exc:
            await rt.send_message("hi", recipient=AgentId("echo", "1"))
        assert exc.value.policy_name == "identity_required"

        await rt.stop()

    async def test_dropped_envelope_returns_none(self) -> None:
        # Trust decay below threshold → silent drop → send returns None.
        rt = LocalRuntime(
            routing_middleware=[
                TrustDecayMiddleware(decay_factor=0.1, quarantine_threshold=0.5),
            ]
        )

        async def handler(ctx: MessageContext, payload: Any) -> str:
            return "served"

        await rt.register("echo", handler)
        await rt._ensure_started()

        # Inject trust on the envelope via an attach middleware.
        class _AttachTrust:
            name = "_attach_trust"

            async def __call__(self, env):
                env.trust = TrustContext(score=0.6, level=TrustLevel.MEDIUM)

        rt._routing_middleware.insert(0, _AttachTrust())

        # 0.6 * 0.1 = 0.06 < 0.5 → drop, returns None
        result = await rt.send_message("hi", recipient=AgentId("echo", "1"))
        assert result is None

        await rt.stop()
