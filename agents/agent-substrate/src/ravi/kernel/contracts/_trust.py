"""Trust and provenance contracts — first-class kernel types.

``PrincipalTrustContext`` — caller identity + trust score + delegation proofs.
``ProvenanceChain``       — lineage of agents/events/workflows that produced a message.
``TrustGraph``            — Protocol the fabric uses to look up a principal's
                            current ``TrustScore`` and ingest fresh signals.

The graph implementation lives in ``ravi.extensions.trust``; the kernel only
defines the contract so every backend (in-memory, Redis, Postgres, Neo4j)
is swappable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ravi.kernel.contracts._coordination import TrustSignal
    from ravi.kernel.runtime._identity import PrincipalId


UTC = timezone.utc


# ---------------------------------------------------------------------------
# TrustScore
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrustScore:
    """Normalized trust score for a principal.

    ``value`` must be in [0.0, 1.0].  Scores are valid only within
    ``decay_seconds`` of ``computed_at``.
    """

    value: float
    source: str
    computed_at: datetime
    decay_seconds: float = 3600.0

    def __post_init__(self) -> None:
        if self.value < 0.0 or self.value > 1.0:
            raise ValueError(
                f"TrustScore.value must be in [0.0, 1.0], got {self.value!r}"
            )

    @property
    def is_stale(self) -> bool:
        """True if the score is older than ``decay_seconds``."""
        return (
            datetime.now(UTC) - self.computed_at
        ).total_seconds() > self.decay_seconds


# ---------------------------------------------------------------------------
# DelegationProof
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DelegationProof:
    """Proof that ``delegator_id`` delegated capabilities to ``delegatee_id``.

    ``scope`` is a list of capability strings.  A scope ending with ``:*``
    is a wildcard that matches any capability sharing the prefix.
    """

    delegator_id: str
    delegatee_id: str
    scope: list[str]
    issued_at: datetime
    expires_at: datetime | None = None
    proof_token: str | None = None

    @property
    def is_valid(self) -> bool:
        """True if the proof has not yet expired."""
        return self.expires_at is None or datetime.now(UTC) <= self.expires_at

    def covers(self, capability: str) -> bool:
        """True if ``capability`` is covered by this delegation's scope.

        Exact match or wildcard: a scope entry ``"ns:*"`` matches any
        capability whose prefix is ``"ns:"`` (e.g. ``"ns:read"``).
        """
        for entry in self.scope:
            if entry == capability:
                return True
            if entry.endswith(":*"):
                prefix = entry[:-1]  # strip the trailing "*"
                if capability.startswith(prefix):
                    return True
        return False


# ---------------------------------------------------------------------------
# PrincipalTrustContext
# ---------------------------------------------------------------------------


@dataclass
class PrincipalTrustContext:
    """Runtime trust credentials for a message principal.

    Not frozen — may be enriched in flight (e.g. adding risk flags after
    a guardrail fires).
    """

    principal_id: str
    trust_score: TrustScore | None = None
    delegations: list[DelegationProof] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)

    @property
    def is_quarantined(self) -> bool:
        """True if ``"quarantined"`` is an active risk flag."""
        return "quarantined" in self.risk_flags

    @property
    def effective_trust(self) -> float:
        """Trust score value, or ``0.0`` when no score is present."""
        return self.trust_score.value if self.trust_score is not None else 0.0

    def can_delegate(self, *, capability: str) -> bool:
        """True if any valid delegation proof covers ``capability``."""
        return any(
            proof.is_valid and proof.covers(capability)
            for proof in self.delegations
        )

    def with_flag(self, flag: str) -> PrincipalTrustContext:
        """Return a new ``PrincipalTrustContext`` with ``flag`` appended to risk_flags."""
        return PrincipalTrustContext(
            principal_id=self.principal_id,
            trust_score=self.trust_score,
            delegations=list(self.delegations),
            risk_flags=[*self.risk_flags, flag],
        )


# ---------------------------------------------------------------------------
# ProvenanceLink
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProvenanceLink:
    """A single step in a provenance chain.

    Records which entity produced a piece of data and what transformation
    (if any) was applied.
    """

    source_id: str
    source_kind: str  # "agent" | "workflow" | "event" | "tool" | "model"
    produced_at: datetime
    transformation: str | None = None


# ---------------------------------------------------------------------------
# ProvenanceChain
# ---------------------------------------------------------------------------


@dataclass
class ProvenanceChain:
    """Ordered lineage of entities that produced a message.

    ``root_event_id`` anchors the chain to the originating event UUID.
    """

    links: list[ProvenanceLink] = field(default_factory=list)
    root_event_id: str | None = None

    @property
    def depth(self) -> int:
        """Number of links in the chain."""
        return len(self.links)

    def append(self, link: ProvenanceLink) -> None:
        """Append *link* to the chain."""
        self.links.append(link)

    @property
    def latest(self) -> ProvenanceLink | None:
        """The most recently appended link, or ``None`` for an empty chain."""
        return self.links[-1] if self.links else None

    def exceeds_depth(self, limit: int) -> bool:
        """True if the chain has more than *limit* links."""
        return self.depth > limit


# ---------------------------------------------------------------------------
# TrustGraph
# ---------------------------------------------------------------------------


@runtime_checkable
class TrustGraph(Protocol):
    """Lookup + ingest contract for principal trust.

    Implementations compose a :class:`TrustScore` from recent :class:`TrustSignal`
    evidence per principal. The fabric calls :meth:`score_for` at envelope
    time (e.g. inside ``TrustEnrichmentMiddleware``) and :meth:`ingest`
    whenever a moderation / verification / reputation event fires.

    :meth:`decay_expired` is a maintenance hook a background sweep can call
    to garbage-collect signals past their ``expires_at`` so memory does not
    grow unbounded.
    """

    async def ingest(
        self, principal_id: "PrincipalId", signal: "TrustSignal"
    ) -> None:
        """Record a fresh signal for ``principal_id``."""
        ...

    async def score_for(
        self, principal_id: "PrincipalId"
    ) -> TrustScore | None:
        """Return the current composite :class:`TrustScore`, or ``None``."""
        ...

    async def signals_for(
        self, principal_id: "PrincipalId"
    ) -> tuple["TrustSignal", ...]:
        """Return all currently-stored signals for ``principal_id``."""
        ...

    async def decay_expired(self) -> int:
        """Drop signals whose ``expires_at`` is in the past. Returns count removed."""
        ...


__all__ = [
    "TrustScore",
    "DelegationProof",
    "PrincipalTrustContext",
    "ProvenanceLink",
    "ProvenanceChain",
    "TrustGraph",
]
