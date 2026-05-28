"""Coordination contracts for temporal semantics, data locality, trust, and placement.

These contracts stay intentionally small and transport-neutral so both
runtime envelopes and inter-service events can share the same language
for time, placement, and routing-level trust.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Literal


def utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class TemporalSemantics:
    """First-class temporal metadata for runtime and event contracts.

    ``event_time`` is the source-of-truth ordering timestamp for the
    event itself. ``not_before`` and ``expires_at`` let transports and
    schedulers express delivery windows. ``replayed_at`` and
    ``logical_time`` are forward-compatible hooks for replay and causal
    sequencing.
    """

    event_time: datetime | None = None
    not_before: datetime | None = None
    expires_at: datetime | None = None
    replayed_at: datetime | None = None
    logical_time: int | None = None

    def bind_defaults(self) -> None:
        """Populate unset values from current time."""
        if self.event_time is None:
            self.event_time = utc_now()

    def is_expired(self, *, now: datetime | None = None) -> bool:
        """Return True when the delivery window has closed."""
        if self.expires_at is None:
            return False
        current = now or utc_now()
        return current > self.expires_at

    def is_ready(self, *, now: datetime | None = None) -> bool:
        """Return True when the delivery window has opened."""
        if self.not_before is None:
            return True
        current = now or utc_now()
        return current >= self.not_before


@dataclass(slots=True)
class LocalityHint:
    """Placement hints for moving compute toward the dominant data set."""

    partition_key: str | None = None
    affinity_key: str | None = None
    region: str | None = None
    placement_scope: Literal["agent", "session", "tenant", "global"] = "agent"


# ── Trust ─────────────────────────────────────────────────────────────────────


class TrustLevel(Enum):
    """Coarse trust tier for fast routing/allocation decisions."""

    UNTRUSTED = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    VERIFIED = 4  # Cryptographically attested identity


@dataclass(frozen=True, slots=True)
class TrustSignal:
    """A single evidence point contributing to a trust score."""

    signal_type: str  # e.g. "moderation_pass", "verification_badge", "reputation_score"
    value: float  # Normalized [0.0, 1.0]
    source_id: str  # Which authority produced this signal
    issued_at: str  # ISO-8601
    expires_at: str | None = None


@dataclass(frozen=True, slots=True)
class TrustContext:
    """
    Trust state for an actor at the time an Envelope was created.
    Carried in the Envelope for routing, ranking, and allocation decisions.
    NOT a signature — that belongs in IdentityContext.
    """

    level: TrustLevel = TrustLevel.MEDIUM
    score: float = 0.5  # Composite [0.0, 1.0]
    signals: tuple[TrustSignal, ...] = field(default_factory=tuple)
    # Tenant-scoped override (e.g. admin bypass)
    tenant_override: TrustLevel | None = None

    @property
    def effective_level(self) -> TrustLevel:
        return self.tenant_override if self.tenant_override is not None else self.level

    def is_at_least(self, minimum: TrustLevel) -> bool:
        return self.effective_level.value >= minimum.value


# ── Placement ─────────────────────────────────────────────────────────────────


class PlacementScope(Enum):
    """Where activation should occur relative to data."""

    LOCAL = auto()    # Same process — ultra-low latency
    SHARD = auto()    # Same shard/partition as dominant data
    REGION = auto()   # Same cloud region
    NEAREST = auto()  # Closest region with replica
    ANY = auto()      # No constraint


@dataclass(frozen=True, slots=True)
class DataGravityHint:
    """
    A single data-gravity anchor: a reference to a data store or partition
    that the activation should be co-located with.
    """

    store_uri: str  # e.g. "postgres://host/db/table", "s3://bucket/prefix"
    partition_key: str  # Shard/partition key within the store
    byte_estimate: int = 0  # Estimated data volume — larger values attract more gravity


@dataclass(frozen=True, slots=True)
class PlacementContract:
    """
    Full placement contract for an agent activation.
    Replaces the minimal LocalityHint for scheduler/allocator use.
    """

    scope: PlacementScope = PlacementScope.ANY
    # Region preference (e.g. "us-east-1")
    region: str | None = None
    # Affinity: schedule near this agent/worker
    affinity_key: str | None = None
    # Anti-affinity: do NOT schedule near this agent/worker
    anti_affinity_key: str | None = None
    # Dominant data anchors sorted by gravity (highest weight first)
    data_gravity: tuple[DataGravityHint, ...] = field(default_factory=tuple)
    # True if activation MUST read archived/cold data (affects tier selection)
    requires_cold_tier: bool = False

    @property
    def primary_gravity(self) -> DataGravityHint | None:
        return self.data_gravity[0] if self.data_gravity else None