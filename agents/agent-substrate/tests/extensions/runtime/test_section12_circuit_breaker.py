"""Section 12 — CircuitBreakerMiddleware tests.

Verifies that the middleware:
- allows envelopes when the circuit is closed
- drops envelopes from principals whose circuit is open
- is a no-op when the envelope has no IdentityContext
- correctly propagates the principal FQN in DropEnvelope
- passes through once the circuit is reset (closed again)
"""

from __future__ import annotations

import pytest

from ravi.extensions.safeguards._in_memory import InMemoryCircuitBreaker
from ravi.extensions.runtime._middleware import CircuitBreakerMiddleware
from ravi.kernel.messages.content import TextBlock
from ravi.kernel.runtime import (
    AgentId,
    Envelope,
    IdentityContext,
    PrincipalId,
    PrincipalKind,
)
from ravi.kernel.runtime._middleware import DropEnvelope


def _identity(name: str = "agent:t1:w1:alice", tenant: str = "t1") -> IdentityContext:
    return IdentityContext(
        principal=PrincipalId(
            kind=PrincipalKind.AGENT,
            tenant_id=tenant,
            workspace_id="w1",
            name=name,
        )
    )


def _envelope(identity: IdentityContext | None = None) -> Envelope:
    return Envelope(
        sender=AgentId(type="agent", key="sender"),
        target=AgentId(type="agent", key="receiver"),
        content=[TextBlock(text="hello")],
        identity=identity,
    )


# ---------------------------------------------------------------------------
# Core behaviour
# ---------------------------------------------------------------------------


async def test_closed_circuit_passes() -> None:
    breaker = InMemoryCircuitBreaker(failure_threshold=3)
    mw = CircuitBreakerMiddleware(circuit_breaker=breaker)
    env = _envelope(identity=_identity())
    # Closed circuit — should not raise.
    await mw(env)


async def test_open_circuit_drops_envelope() -> None:
    breaker = InMemoryCircuitBreaker(failure_threshold=2)
    identity = _identity("agent:t1:w1:bad")
    principal_fqn = identity.principal.fqn

    # Open the circuit by recording failures.
    await breaker.record_failure(principal_fqn)
    await breaker.record_failure(principal_fqn)

    mw = CircuitBreakerMiddleware(circuit_breaker=breaker)
    with pytest.raises(DropEnvelope):
        await mw(_envelope(identity=identity))


async def test_no_identity_is_noop() -> None:
    breaker = InMemoryCircuitBreaker()
    mw = CircuitBreakerMiddleware(circuit_breaker=breaker)
    env = _envelope(identity=None)
    # No identity — middleware must be a no-op.
    await mw(env)


async def test_drop_envelope_contains_principal_fqn() -> None:
    breaker = InMemoryCircuitBreaker(failure_threshold=1)
    identity = _identity("agent:t1:w1:culprit")
    fqn = identity.principal.fqn

    await breaker.record_failure(fqn)

    mw = CircuitBreakerMiddleware(circuit_breaker=breaker)
    with pytest.raises(DropEnvelope) as exc_info:
        await mw(_envelope(identity=identity))

    assert "culprit" in str(exc_info.value)


async def test_reset_closes_circuit() -> None:
    breaker = InMemoryCircuitBreaker(failure_threshold=1)
    identity = _identity("agent:t1:w1:flaky")
    fqn = identity.principal.fqn

    await breaker.record_failure(fqn)

    mw = CircuitBreakerMiddleware(circuit_breaker=breaker)
    with pytest.raises(DropEnvelope):
        await mw(_envelope(identity=identity))

    # Operator resets the circuit.
    await breaker.reset(fqn)

    # Now the envelope should pass.
    await mw(_envelope(identity=identity))


async def test_name_attribute() -> None:
    mw = CircuitBreakerMiddleware(circuit_breaker=InMemoryCircuitBreaker())
    assert mw.name == "circuit_breaker"


async def test_multiple_principals_independent() -> None:
    breaker = InMemoryCircuitBreaker(failure_threshold=2)
    bad = _identity("agent:t1:w1:bad")
    good = _identity("agent:t1:w1:good")

    # Only the bad principal trips the breaker.
    await breaker.record_failure(bad.principal.fqn)
    await breaker.record_failure(bad.principal.fqn)

    mw = CircuitBreakerMiddleware(circuit_breaker=breaker)
    # Good principal still passes.
    await mw(_envelope(identity=good))
    # Bad principal is dropped.
    with pytest.raises(DropEnvelope):
        await mw(_envelope(identity=bad))
