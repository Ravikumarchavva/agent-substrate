"""Operator kill-switch contracts.

Kill switches are operator-controlled rules that halt matching envelope
traffic before it reaches side-effecting runtime paths. The kernel defines
the neutral target/rule/decision shapes; activation state lives in extensions.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Protocol, runtime_checkable

from ravi.kernel.runtime._message import Envelope

__all__ = [
    "KillSwitchDecision",
    "KillSwitchRule",
    "KillSwitchScope",
    "KillSwitchTarget",
    "OperatorKillSwitch",
]


UTC = timezone.utc


def _utc_now() -> datetime:
    return datetime.now(UTC)


class KillSwitchScope(Enum):
    """Field matched by a kill-switch rule."""

    GLOBAL = auto()
    TENANT = auto()
    WORKSPACE = auto()
    ACTOR = auto()
    SENDER = auto()
    TARGET = auto()
    CORRELATION = auto()
    ENVELOPE = auto()
    EVENT_TYPE = auto()


@dataclass(frozen=True, slots=True)
class KillSwitchTarget:
    """Fields a kill-switch implementation can match against."""

    envelope_id: str | None = None
    correlation_id: str | None = None
    tenant_id: str | None = None
    workspace_id: str | None = None
    actor_id: str | None = None
    sender: str | None = None
    target: str | None = None
    event_type: str | None = None

    @classmethod
    def from_envelope(cls, envelope: Envelope) -> KillSwitchTarget:
        """Create a kill-switch target snapshot from an :class:`Envelope`.

        Tenancy and actor identity are derived from ``envelope.identity`` when
        present — the in-process envelope no longer carries denormalised
        tenant/workspace/actor fields.
        """
        identity = envelope.identity
        return cls(
            envelope_id=envelope.correlation_id,
            correlation_id=envelope.correlation_id,
            tenant_id=identity.effective_tenant_id if identity is not None else None,
            workspace_id=identity.effective_workspace_id if identity is not None else None,
            actor_id=identity.principal.fqn if identity is not None else None,
            sender=str(envelope.sender) if envelope.sender is not None else None,
            target=str(envelope.target) if envelope.target is not None else None,
            event_type=None,
        )


@dataclass(frozen=True, slots=True)
class KillSwitchRule:
    """Operator-managed rule that blocks matching targets."""

    scope: KillSwitchScope
    value: str
    reason: str
    activated_by: str
    switch_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    activated_at: datetime = field(default_factory=_utc_now)
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.scope is not KillSwitchScope.GLOBAL and not self.value:
            raise ValueError("non-global kill switches require a value")
        if not self.reason:
            raise ValueError("reason is required")
        if not self.activated_by:
            raise ValueError("activated_by is required")

    @property
    def is_expired(self) -> bool:
        """True when ``expires_at`` has passed."""

        return self.expires_at is not None and self.expires_at <= _utc_now()


@dataclass(frozen=True, slots=True)
class KillSwitchDecision:
    """Result of checking a target against active kill switches."""

    blocked: bool
    reason: str = ""
    switch_id: str | None = None
    scope: KillSwitchScope | None = None
    matched_value: str | None = None


@runtime_checkable
class OperatorKillSwitch(Protocol):
    """Activation and matching contract for operator kill switches."""

    async def activate(self, rule: KillSwitchRule) -> KillSwitchRule:
        """Activate or replace a kill switch."""
        ...

    async def deactivate(self, switch_id: str) -> bool:
        """Deactivate a switch. Return ``True`` when one existed."""
        ...

    async def check(self, target: KillSwitchTarget) -> KillSwitchDecision:
        """Return whether ``target`` is currently blocked."""
        ...

    async def active_switches(self) -> tuple[KillSwitchRule, ...]:
        """Return a snapshot of active, unexpired switches."""
        ...
