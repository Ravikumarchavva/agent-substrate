"""Built-in routing middlewares for the agent runtime.

These compose the safety-net every multi-tenant agent fabric needs:

- :class:`IdentityRequiredMiddleware`  — reject anonymous envelopes
- :class:`TenantIsolationMiddleware`   — reject cross-tenant addressing
- :class:`DepthLimitMiddleware`        — refuse runaway recursive activation
- :class:`TrustDecayMiddleware`        — decay trust per hop; quarantine below threshold

Compose them on a :class:`LocalRuntime` (or :class:`DistributedRuntime`)
via the ``routing_middleware=[...]`` constructor argument. Order matters:
identity should come first so downstream middleware can rely on
``envelope.identity`` being non-None.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from ravi.kernel.contracts._coordination import TrustContext, TrustLevel
from ravi.kernel.contracts._trust import TrustGraph
from ravi.kernel.runtime._contracts import Envelope
from ravi.kernel.runtime._identity import AgentId
from ravi.kernel.runtime._middleware import (
    DropEnvelope,
    RoutingMiddlewareRejection,
)

__all__ = [
    "IdentityRequiredMiddleware",
    "TenantIsolationMiddleware",
    "DepthLimitMiddleware",
    "TrustDecayMiddleware",
    "TrustEnrichmentMiddleware",
]


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Identity required
# ---------------------------------------------------------------------------


class IdentityRequiredMiddleware:
    """Reject envelopes that arrive without a verified ``IdentityContext``.

    Use as the first middleware at trust boundaries (HTTP / API entry points
    that should have already attached an ``IdentityContext`` to the envelope).
    Internal envelopes that the runtime constructs itself can be exempted
    via ``allow_internal_sender_types``.
    """

    name = "identity_required"

    def __init__(
        self,
        *,
        allow_internal_sender_types: tuple[str, ...] = (),
    ) -> None:
        self._allow = set(allow_internal_sender_types)

    async def __call__(self, envelope: Envelope) -> None:
        if envelope.identity is not None:
            return
        sender = envelope.sender
        if sender is not None and sender.type in self._allow:
            return
        raise RoutingMiddlewareRejection(
            self.name,
            (
                f"envelope {envelope.correlation_id} has no IdentityContext "
                f"and sender {sender} is not on the internal allow-list"
            ),
        )


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


class TenantIsolationMiddleware:
    """Refuse envelopes that cross tenant boundaries.

    Compares ``envelope.identity.principal.tenant_id`` against either:

    - the recipient ``AgentId``'s parsed tenant prefix (when the key is
      formatted ``"{tenant_id}:{rest}"``), or
    - an injected ``tenant_resolver`` callback.

    Falls open (allows) when no resolver is provided and the recipient
    key has no tenant prefix — log a warning so operators notice the gap.
    """

    name = "tenant_isolation"

    def __init__(
        self,
        *,
        tenant_resolver=None,  # type: ignore[no-untyped-def]
    ) -> None:
        self._resolver = tenant_resolver

    async def __call__(self, envelope: Envelope) -> None:
        if envelope.identity is None:
            # Identity required runs first; if it didn't fire, the runtime
            # configured this middleware without an identity gate. Bail out
            # of *this* middleware rather than silently allow.
            raise RoutingMiddlewareRejection(
                self.name,
                "no IdentityContext on envelope — cannot enforce tenant isolation",
            )

        sender_tenant = envelope.identity.principal.tenant_id
        target_tenant = self._target_tenant(envelope)
        if target_tenant is None:
            logger.warning(
                "tenant_isolation: envelope %s has no resolvable target tenant; allowing",
                envelope.correlation_id,
            )
            return
        if sender_tenant != target_tenant:
            raise RoutingMiddlewareRejection(
                self.name,
                (
                    f"envelope {envelope.correlation_id}: tenant "
                    f"{sender_tenant!r} cannot address tenant {target_tenant!r}"
                ),
            )

    def _target_tenant(self, envelope: Envelope) -> str | None:
        target = envelope.target
        if not isinstance(target, AgentId):
            return None
        if self._resolver is not None:
            return self._resolver(target)
        if ":" in target.key:
            return target.key.split(":", 1)[0]
        return None


# ---------------------------------------------------------------------------
# Depth limit
# ---------------------------------------------------------------------------


class DepthLimitMiddleware:
    """Enforce the per-causal-chain activation depth ceiling.

    ``AgentActivationContract.depth`` increments per hop; this middleware
    refuses any envelope whose activation depth meets or exceeds
    ``AgentActivationContract.max_depth`` (or the global ``hard_ceiling``,
    whichever is lower). Protects against agents that recursively spawn
    themselves into an unbounded fan-out.
    """

    name = "depth_limit"

    def __init__(self, *, hard_ceiling: int = 64) -> None:
        if hard_ceiling <= 0:
            raise ValueError(
                f"hard_ceiling must be > 0, got {hard_ceiling!r}"
            )
        self._hard_ceiling = hard_ceiling

    async def __call__(self, envelope: Envelope) -> None:
        activation = envelope.activation
        if activation is None:
            return  # No activation contract → no depth to enforce
        max_allowed = min(activation.max_depth, self._hard_ceiling)
        if activation.depth >= max_allowed:
            raise RoutingMiddlewareRejection(
                self.name,
                (
                    f"envelope {envelope.correlation_id}: activation depth "
                    f"{activation.depth} >= ceiling {max_allowed}"
                ),
            )


# ---------------------------------------------------------------------------
# Trust decay
# ---------------------------------------------------------------------------


class TrustDecayMiddleware:
    """Decay trust score per hop; quarantine envelopes that fall below threshold.

    Each pass multiplies ``envelope.trust.score`` by ``decay_factor`` (default
    0.95) and rebuilds the envelope's ``trust`` field as a fresh
    :class:`TrustContext`. When the resulting score is below
    ``quarantine_threshold``, the envelope is silently dropped (caller sees
    ``None`` from ``send_message``) — never delivered.

    Operates only on envelopes that already carry a ``TrustContext``;
    envelopes without one pass through untouched.
    """

    name = "trust_decay"

    def __init__(
        self,
        *,
        decay_factor: float = 0.95,
        quarantine_threshold: float = 0.1,
    ) -> None:
        if not 0.0 < decay_factor <= 1.0:
            raise ValueError(
                f"decay_factor must be in (0, 1], got {decay_factor!r}"
            )
        if not 0.0 <= quarantine_threshold <= 1.0:
            raise ValueError(
                f"quarantine_threshold must be in [0, 1], got "
                f"{quarantine_threshold!r}"
            )
        self._decay = decay_factor
        self._threshold = quarantine_threshold

    async def __call__(self, envelope: Envelope) -> None:
        if envelope.trust is None:
            return

        decayed_score = envelope.trust.score * self._decay

        if decayed_score < self._threshold:
            raise DropEnvelope(
                self.name,
                (
                    f"trust score {decayed_score:.3f} below quarantine "
                    f"threshold {self._threshold:.3f}"
                ),
            )

        envelope.trust = replace(
            envelope.trust,
            score=decayed_score,
            level=_level_for_score(decayed_score),
        )


def _level_for_score(score: float) -> TrustLevel:
    """Map a numeric score back to a coarse :class:`TrustLevel` tier."""
    if score >= 0.9:
        return TrustLevel.VERIFIED
    if score >= 0.7:
        return TrustLevel.HIGH
    if score >= 0.4:
        return TrustLevel.MEDIUM
    if score >= 0.2:
        return TrustLevel.LOW
    return TrustLevel.UNTRUSTED


# ---------------------------------------------------------------------------
# Trust enrichment
# ---------------------------------------------------------------------------


class TrustEnrichmentMiddleware:
    """Look up the sender's :class:`TrustScore` and attach it to the envelope.

    Sits between identity gates and :class:`TrustDecayMiddleware`. When the
    envelope already carries a ``TrustContext`` (e.g. a remote forward
    already enriched), the middleware is a no-op so we don't double-decay
    on the receiving side. When the envelope has no ``IdentityContext`` we
    skip silently — identity-required middleware runs upstream and will
    have already rejected anonymous envelopes if configured.

    Falls open with a configurable ``default_level`` when the graph has
    no score for the principal — typically ``TrustLevel.LOW`` so unknown
    principals start cautious without being instantly quarantined.
    """

    name = "trust_enrichment"

    def __init__(
        self,
        *,
        trust_graph: TrustGraph,
        default_level: TrustLevel = TrustLevel.LOW,
        default_score: float = 0.3,
    ) -> None:
        if not 0.0 <= default_score <= 1.0:
            raise ValueError(
                f"default_score must be in [0, 1], got {default_score!r}"
            )
        self._graph = trust_graph
        self._default_level = default_level
        self._default_score = default_score

    async def __call__(self, envelope: Envelope) -> None:
        if envelope.trust is not None:
            return  # Already enriched (e.g. by a cross-worker forward)
        if envelope.identity is None:
            return  # Nothing to look up

        principal = envelope.identity.principal
        score = await self._graph.score_for(principal)
        if score is None:
            envelope.trust = TrustContext(
                score=self._default_score,
                level=self._default_level,
            )
            return

        envelope.trust = TrustContext(
            score=score.value,
            level=_level_for_score(score.value),
        )
