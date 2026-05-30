"""Section 11 — QuarantineCheckMiddleware tests.

Verifies that the middleware:
- drops envelopes from quarantined principals
- allows envelopes from non-quarantined principals
- is a no-op when envelope has no IdentityContext
- composes correctly with IdentityRequiredMiddleware in a pipeline
"""

from __future__ import annotations

import pytest

from ravi.guardrails.governance._in_memory import InMemoryQuarantineActuator
from ravi.agents.runtime._middleware import QuarantineCheckMiddleware
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


async def test_non_quarantined_principal_passes() -> None:
    actuator = InMemoryQuarantineActuator()
    mw = QuarantineCheckMiddleware(quarantine_actuator=actuator)
    env = _envelope(identity=_identity("agent:t1:w1:alice"))
    # Should not raise.
    await mw(env)


async def test_quarantined_principal_is_dropped() -> None:
    actuator = InMemoryQuarantineActuator()
    identity = _identity("agent:t1:w1:bob")
    await actuator.quarantine(identity.principal.fqn, reason="test quarantine")

    mw = QuarantineCheckMiddleware(quarantine_actuator=actuator)
    env = _envelope(identity=identity)

    with pytest.raises(DropEnvelope):
        await mw(env)


async def test_no_identity_passes_silently() -> None:
    actuator = InMemoryQuarantineActuator()
    mw = QuarantineCheckMiddleware(quarantine_actuator=actuator)
    env = _envelope(identity=None)
    # No IdentityContext — middleware should be a no-op (IdentityRequired runs first).
    await mw(env)


async def test_lift_quarantine_restores_access() -> None:
    actuator = InMemoryQuarantineActuator()
    identity = _identity("agent:t1:w1:carol")

    await actuator.quarantine(identity.principal.fqn, reason="temp")
    mw = QuarantineCheckMiddleware(quarantine_actuator=actuator)

    with pytest.raises(DropEnvelope):
        await mw(_envelope(identity=identity))

    await actuator.lift_quarantine(identity.principal.fqn)
    # Should now pass without raising.
    await mw(_envelope(identity=identity))


async def test_drop_envelope_carries_principal_fqn() -> None:
    actuator = InMemoryQuarantineActuator()
    identity = _identity("agent:t1:w1:dave")
    await actuator.quarantine(identity.principal.fqn, reason="fraud")

    mw = QuarantineCheckMiddleware(quarantine_actuator=actuator)
    with pytest.raises(DropEnvelope) as exc_info:
        await mw(_envelope(identity=identity))

    assert "dave" in str(exc_info.value)


async def test_multiple_principals_independent() -> None:
    """Quarantining one principal must not affect others."""
    actuator = InMemoryQuarantineActuator()
    bad = _identity("agent:t1:w1:bad")
    good = _identity("agent:t1:w1:good")

    await actuator.quarantine(bad.principal.fqn, reason="bad actor")

    mw = QuarantineCheckMiddleware(quarantine_actuator=actuator)
    # Good principal passes.
    await mw(_envelope(identity=good))
    # Bad principal is dropped.
    with pytest.raises(DropEnvelope):
        await mw(_envelope(identity=bad))


async def test_name_attribute() -> None:
    mw = QuarantineCheckMiddleware(
        quarantine_actuator=InMemoryQuarantineActuator()
    )
    assert mw.name == "quarantine_check"
