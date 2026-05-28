"""Section 6 regression tests — TrustEnrichmentMiddleware + composed trust chain.

The full chain on a real runtime:

    IdentityRequired
        → TrustEnrichment (graph lookup → attach TrustContext)
            → TrustDecay (per-hop decay; quarantine below threshold)
                → dispatch

Tests:

- Enrichment attaches when envelope has identity but no trust
- Enrichment is a no-op when trust already present (cross-worker forwarded envelopes)
- Enrichment skips when identity missing
- Unknown principal gets the default level/score
- High-trust principal flows through the full chain
- Low-trust principal is quarantined by the trust-decay middleware
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from ravi.extensions.runtime import (
    IdentityRequiredMiddleware,
    TrustDecayMiddleware,
    TrustEnrichmentMiddleware,
)
from ravi.extensions.trust import InMemoryTrustGraph
from ravi.kernel.contracts._coordination import (
    TrustContext,
    TrustLevel,
    TrustSignal,
)
from ravi.kernel.messages.content import TextBlock
from ravi.kernel.runtime import (
    AgentId,
    Envelope,
    IdentityContext,
    LocalRuntime,
    MessageContext,
    PrincipalId,
    PrincipalKind,
)


UTC = timezone.utc


_PRINCIPAL_CACHE: dict[str, PrincipalId] = {}


def _principal(name: str = "alice") -> PrincipalId:
    """Stable, name-keyed principal so repeated calls share a fingerprint.

    ``PrincipalId.uid`` is a random UUID by default, so constructing a fresh
    one each call yields different fingerprints — the trust graph would
    treat them as distinct principals. The cache keeps a single canonical
    ``PrincipalId`` per name within the test process.
    """
    if name not in _PRINCIPAL_CACHE:
        _PRINCIPAL_CACHE[name] = PrincipalId(
            kind=PrincipalKind.AGENT,
            tenant_id="t1",
            workspace_id="w1",
            name=name,
        )
    return _PRINCIPAL_CACHE[name]


def _identity(name: str = "alice") -> IdentityContext:
    return IdentityContext(principal=_principal(name))


def _signal(value: float) -> TrustSignal:
    return TrustSignal(
        signal_type="moderation_pass",
        value=value,
        source_id="test",
        issued_at=datetime.now(UTC).isoformat(),
    )


def _env(
    *,
    identity: IdentityContext | None = None,
    trust: TrustContext | None = None,
) -> Envelope:
    return Envelope(
        sender=None,
        target=AgentId("echo", "1"),
        content=[TextBlock(text="x")],
        identity=identity,
        trust=trust,
    )


# ---------------------------------------------------------------------------
# Middleware unit tests
# ---------------------------------------------------------------------------


class TestTrustEnrichmentMiddleware:
    async def test_attaches_trust_when_identity_present(self) -> None:
        graph = InMemoryTrustGraph()
        await graph.ingest(_principal(), _signal(0.85))
        mw = TrustEnrichmentMiddleware(trust_graph=graph)

        env = _env(identity=_identity())
        await mw(env)

        assert env.trust is not None
        assert env.trust.score == pytest.approx(0.85, abs=0.01)
        assert env.trust.level is TrustLevel.HIGH

    async def test_no_op_when_trust_already_present(self) -> None:
        graph = InMemoryTrustGraph()
        await graph.ingest(_principal(), _signal(0.1))  # would override to LOW
        mw = TrustEnrichmentMiddleware(trust_graph=graph)

        pre = TrustContext(score=0.95, level=TrustLevel.VERIFIED)
        env = _env(identity=_identity(), trust=pre)
        await mw(env)

        # Existing trust preserved (cross-worker forward already enriched)
        assert env.trust is pre

    async def test_no_op_when_identity_missing(self) -> None:
        graph = InMemoryTrustGraph()
        mw = TrustEnrichmentMiddleware(trust_graph=graph)
        env = _env(identity=None)
        await mw(env)
        assert env.trust is None

    async def test_unknown_principal_gets_default(self) -> None:
        graph = InMemoryTrustGraph()
        mw = TrustEnrichmentMiddleware(
            trust_graph=graph,
            default_level=TrustLevel.LOW,
            default_score=0.3,
        )

        env = _env(identity=_identity())
        await mw(env)
        assert env.trust is not None
        assert env.trust.score == 0.3
        assert env.trust.level is TrustLevel.LOW

    async def test_invalid_default_score_rejected(self) -> None:
        graph = InMemoryTrustGraph()
        with pytest.raises(ValueError):
            TrustEnrichmentMiddleware(trust_graph=graph, default_score=1.5)


# ---------------------------------------------------------------------------
# End-to-end on a real runtime
# ---------------------------------------------------------------------------


class TestComposedTrustChain:
    async def test_high_trust_principal_flows_through(self) -> None:
        graph = InMemoryTrustGraph()
        await graph.ingest(_principal(), _signal(0.95))

        rt = LocalRuntime(
            routing_middleware=[
                IdentityRequiredMiddleware(),
                TrustEnrichmentMiddleware(trust_graph=graph),
                TrustDecayMiddleware(
                    decay_factor=0.9, quarantine_threshold=0.5
                ),
            ]
        )

        delivered: list[float] = []

        async def handler(ctx: MessageContext, payload: Any) -> str:
            return "served"

        await rt.register("echo", handler)
        await rt._ensure_started()

        # Inject identity at the front of the chain (the HTTP boundary does
        # this normally; tests stub it inline).
        class _AttachIdentity:
            name = "_test_attach_identity"

            async def __call__(self, env):
                env.identity = _identity()
                delivered.append(0.0)  # marker — middleware ran

        rt._routing_middleware.insert(0, _AttachIdentity())

        result = await rt.send_message(
            "hi", recipient=AgentId("echo", "1")
        )
        assert result == "served"
        await rt.stop()

    async def test_low_trust_principal_is_quarantined(self) -> None:
        graph = InMemoryTrustGraph()
        # Build up several low signals so the composite is low
        for _ in range(3):
            await graph.ingest(_principal("eve"), _signal(0.1))

        rt = LocalRuntime(
            routing_middleware=[
                IdentityRequiredMiddleware(),
                TrustEnrichmentMiddleware(trust_graph=graph),
                TrustDecayMiddleware(
                    decay_factor=0.9, quarantine_threshold=0.5
                ),
            ]
        )

        served = False

        async def handler(ctx: MessageContext, payload: Any) -> str:
            nonlocal served
            served = True
            return "served"

        await rt.register("echo", handler)
        await rt._ensure_started()

        class _AttachEve:
            name = "_test_attach_eve"

            async def __call__(self, env):
                env.identity = _identity("eve")

        rt._routing_middleware.insert(0, _AttachEve())

        # Eve's trust starts at ~0.1, decays to ~0.09, falls below 0.5 → drop
        result = await rt.send_message(
            "hi", recipient=AgentId("echo", "1")
        )
        assert result is None  # silent drop, never reached handler
        assert served is False
        await rt.stop()

    async def test_unknown_principal_uses_default_and_can_be_quarantined(
        self,
    ) -> None:
        graph = InMemoryTrustGraph()  # empty — unknown principal

        rt = LocalRuntime(
            routing_middleware=[
                IdentityRequiredMiddleware(),
                TrustEnrichmentMiddleware(
                    trust_graph=graph,
                    default_score=0.2,  # below quarantine
                    default_level=TrustLevel.LOW,
                ),
                TrustDecayMiddleware(
                    decay_factor=1.0, quarantine_threshold=0.3
                ),
            ]
        )

        async def handler(ctx: MessageContext, payload: Any) -> str:
            return "served"

        await rt.register("echo", handler)
        await rt._ensure_started()

        class _AttachUnknown:
            name = "_test_attach_unknown"

            async def __call__(self, env):
                env.identity = _identity("randomer")

        rt._routing_middleware.insert(0, _AttachUnknown())

        # Default score 0.2 < threshold 0.3 → drop
        result = await rt.send_message(
            "hi", recipient=AgentId("echo", "1")
        )
        assert result is None
        await rt.stop()
