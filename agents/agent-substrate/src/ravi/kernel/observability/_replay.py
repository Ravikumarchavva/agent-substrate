"""Replay admission contracts.

Replay is powerful enough to duplicate side effects, so every replay attempt
must pass through an admission gate with idempotency. The kernel keeps the
contract transport-neutral; stores and operator tooling live above it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Protocol, runtime_checkable

__all__ = [
    "ReplayAdmission",
    "ReplayAdmissionStatus",
    "ReplayDenyRule",
    "ReplayGate",
    "ReplayRequest",
]


UTC = timezone.utc


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ReplayAdmissionStatus(Enum):
    """Outcome class for a replay admission decision."""

    ALLOWED = auto()
    DENIED = auto()
    DUPLICATE = auto()


@dataclass(frozen=True, slots=True)
class ReplayRequest:
    """A request to replay a previously-observed envelope."""

    envelope_id: str
    correlation_id: str
    requested_by: str
    reason: str
    idempotency_key: str = field(default_factory=lambda: uuid.uuid4().hex)
    requested_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not self.envelope_id:
            raise ValueError("envelope_id is required")
        if not self.correlation_id:
            raise ValueError("correlation_id is required")
        if not self.requested_by:
            raise ValueError("requested_by is required")
        if not self.reason:
            raise ValueError("reason is required")


@dataclass(frozen=True, slots=True)
class ReplayDenyRule:
    """Operator rule that denies replay for an envelope or correlation."""

    reason: str
    created_by: str
    envelope_id: str | None = None
    correlation_id: str | None = None
    rule_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not self.reason:
            raise ValueError("reason is required")
        if not self.created_by:
            raise ValueError("created_by is required")
        if self.envelope_id is None and self.correlation_id is None:
            raise ValueError("deny rule must target an envelope or correlation")

    def matches(self, request: ReplayRequest) -> bool:
        """True when this rule applies to ``request``."""

        return (
            self.envelope_id == request.envelope_id
            if self.envelope_id is not None
            else False
        ) or (
            self.correlation_id == request.correlation_id
            if self.correlation_id is not None
            else False
        )


@dataclass(frozen=True, slots=True)
class ReplayAdmission:
    """Admission decision for one replay idempotency key."""

    idempotency_key: str
    envelope_id: str
    correlation_id: str
    allowed: bool
    status: ReplayAdmissionStatus
    reason: str
    decided_at: datetime
    replay_token: str | None = None


@runtime_checkable
class ReplayGate(Protocol):
    """Idempotent replay admission gate."""

    async def admit(self, request: ReplayRequest) -> ReplayAdmission:
        """Allow or deny ``request``; repeat keys must not create new tokens."""
        ...

    async def admission_for(
        self, idempotency_key: str
    ) -> ReplayAdmission | None:
        """Return the original decision for ``idempotency_key`` if known."""
        ...

    async def deny(self, rule: ReplayDenyRule) -> None:
        """Add or replace an operator deny rule."""
        ...

    async def clear_denial(self, rule_id: str) -> bool:
        """Remove a deny rule. Return ``True`` when one existed."""
        ...
