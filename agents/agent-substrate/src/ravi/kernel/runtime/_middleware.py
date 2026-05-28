"""Routing middleware — pre-dispatch envelope gates.

Every envelope flowing through the runtime passes through an ordered chain
of :class:`RoutingMiddleware` before it is dispatched to the recipient.
Middleware can:

- **Inspect** the envelope (identity, trust, provenance, activation depth)
- **Reject** it by raising :class:`RoutingMiddlewareRejection`
- **Enrich** it in place (e.g. decay the trust score per hop)

Use cases
~~~~~~~~~
- ``IdentityRequiredMiddleware`` — refuse envelopes missing ``IdentityContext``
- ``TenantIsolationMiddleware`` — refuse cross-tenant addressing
- ``DepthLimitMiddleware`` — enforce recursion ceiling per causal chain
- ``TrustDecayMiddleware`` — decay trust score per hop, quarantine below threshold

The kernel only ships the contract. Concrete middlewares live in
``ravi.extensions.runtime.middleware`` so they can be swapped without
touching the kernel.

Concurrency
~~~~~~~~~~~
Middleware ``__call__`` runs on the dispatch path. Implementations must
either be stateless or guard their own state — the runtime does not lock
around the invocation. Keep them cheap; everything happens before the
envelope is enqueued for the target agent.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ravi.kernel.runtime._contracts import Envelope

__all__ = [
    "RoutingMiddleware",
    "RoutingMiddlewareRejection",
    "DropEnvelope",
]


class RoutingMiddlewareRejection(Exception):
    """Raised by a :class:`RoutingMiddleware` to reject an envelope.

    The runtime catches this in ``send_message`` and ``publish_message``
    and surfaces it to the caller. Carries a ``policy_name`` so the
    operator can attribute rejections to a specific middleware.
    """

    def __init__(self, policy_name: str, reason: str) -> None:
        self.policy_name = policy_name
        self.reason = reason
        super().__init__(f"{policy_name}: {reason}")


class DropEnvelope(Exception):
    """Raised by a middleware to silently drop an envelope.

    Distinct from :class:`RoutingMiddlewareRejection`: a drop is *expected*
    (rate-limit, idempotency dedup, trust-quarantine fallthrough) and
    should not surface as an error to the caller. The runtime returns
    ``None`` and logs at INFO instead.
    """

    def __init__(self, policy_name: str, reason: str) -> None:
        self.policy_name = policy_name
        self.reason = reason
        super().__init__(f"{policy_name}: {reason}")


@runtime_checkable
class RoutingMiddleware(Protocol):
    """Pre-dispatch envelope gate.

    Implementations should expose ``name`` for log / metric attribution.
    ``__call__`` is invoked once per envelope in chain order. May mutate
    the envelope's mutable fields (``trust``, ``identity``, ``metadata``)
    but must not change ``target`` or ``content``.
    """

    name: str

    async def __call__(self, envelope: Envelope) -> None:
        """Inspect / enrich / reject *envelope*.

        Return normally to allow delivery. Raise
        :class:`RoutingMiddlewareRejection` to fail the call. Raise
        :class:`DropEnvelope` to silently swallow it.
        """
        ...
