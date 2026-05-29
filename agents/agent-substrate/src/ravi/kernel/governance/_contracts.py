"""Governance + Political Dynamics kernel contracts — Section 11.

The governance plane answers: *is the current multi-agent configuration safe
to operate?*  It detects emergent collusion, scores systemic risk, and
provides actuators so an operator (or the platform itself) can quarantine
misbehaving agents without taking down the whole system.

Key concepts
------------
``Coalition``
    A named group of principals that appear to be coordinating.  The
    coalition detector identifies clusters of principals whose message
    patterns suggest coordinated behaviour (e.g., timing correlation, shared
    framing, mutual amplification).

``RiskScore``
    A normalised [0, 1] measure of systemic danger for a principal or a
    coalition.  Scores above a configurable threshold trigger governance
    actions.

``GovernanceAction``
    What the governance plane decides to do: ALLOW, WARN, THROTTLE, or
    QUARANTINE.  A quarantined principal is not allowed to send or receive
    messages.

``QuarantineActuator``
    Protocol that actually enforces quarantine decisions.  The kernel defines
    only the interface; the implementation may talk to the scheduler, the
    routing middleware, or the identity store.

``GovernancePolicy``
    High-level coordinator: takes a principal + evidence, returns a
    ``GovernanceDecision``.

Design constraints
------------------
* Zero concrete logic — only dataclasses, enums, and Protocols.
* No external imports — only stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Protocol, Sequence, runtime_checkable

__all__ = [
    "CoalitionKind",
    "GovernanceAction",
    "Coalition",
    "RiskScore",
    "GovernanceEvidence",
    "GovernanceDecision",
    "QuarantineActuator",
    "CoalitionDetector",
    "GovernancePolicy",
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class CoalitionKind(Enum):
    """Characterises why a group of principals is classified as a coalition."""

    TIMING_CORRELATED = auto()
    """Messages arrive in tight temporal clusters suggesting coordination."""

    CONTENT_AMPLIFICATION = auto()
    """Multiple principals repeatedly endorse the same content."""

    TRUST_INFLATION = auto()
    """Principals mutually raise each other's trust scores."""

    RESOURCE_FARMING = auto()
    """Principals share token budgets or circumvent per-principal caps."""

    UNKNOWN = auto()


class GovernanceAction(Enum):
    """Governance decision for a principal or coalition."""

    ALLOW = auto()
    """Risk is acceptable; no intervention."""

    WARN = auto()
    """Log a warning; no operational change."""

    THROTTLE = auto()
    """Reduce the principal's scheduler share or message rate."""

    QUARANTINE = auto()
    """Isolate the principal; block sending and receiving."""


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Coalition:
    """A detected group of potentially colluding principals."""

    coalition_id: str
    member_fqns: tuple[str, ...]
    kind: CoalitionKind
    confidence: float
    """Detection confidence in [0, 1]."""
    detected_at: str
    """ISO-8601 UTC timestamp."""
    evidence_summary: str = ""


@dataclass(frozen=True, slots=True)
class RiskScore:
    """Point-in-time risk assessment for a principal."""

    principal_fqn: str
    score: float
    """Normalised risk in [0, 1].  1 = maximum danger."""
    contributors: tuple[str, ...]
    """Short labels explaining the main risk contributors."""
    scored_at: str
    """ISO-8601 UTC timestamp."""


@dataclass(frozen=True, slots=True)
class GovernanceEvidence:
    """Bundle of signals fed into a governance policy evaluation."""

    principal_fqn: str
    risk_score: RiskScore
    active_coalitions: tuple[Coalition, ...]
    recent_violation_count: int = 0


@dataclass(frozen=True, slots=True)
class GovernanceDecision:
    """Outcome of a governance policy evaluation."""

    principal_fqn: str
    action: GovernanceAction
    rationale: str
    decided_at: str
    """ISO-8601 UTC timestamp."""
    coalition_id: str | None = None
    """Set when the decision was triggered by a coalition, not solo risk."""


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class QuarantineActuator(Protocol):
    """Enforces quarantine decisions.

    Implementations may talk to the scheduler (revoke slots), the routing
    middleware (drop envelopes), or the identity store (deactivate principal).
    """

    async def quarantine(self, principal_fqn: str, reason: str) -> None:
        """Isolate ``principal_fqn`` from the message bus.

        Idempotent — quarantining an already-quarantined principal is a no-op.
        """
        ...

    async def lift_quarantine(self, principal_fqn: str) -> None:
        """Restore ``principal_fqn`` to normal operation.

        Raises :class:`KeyError` when the principal was not quarantined.
        """
        ...

    async def is_quarantined(self, principal_fqn: str) -> bool:
        """Return ``True`` when the principal is currently quarantined."""
        ...


@runtime_checkable
class CoalitionDetector(Protocol):
    """Detects emergent coalitions from a stream of principal activity."""

    async def observe(
        self,
        principal_fqn: str,
        event_type: str,
        *,
        counterparty_fqn: str | None = None,
        timestamp_utc: str,
    ) -> None:
        """Record one observable event for coalition analysis.

        ``event_type`` is a free-form label (e.g., ``"message_sent"``,
        ``"trust_vote_up"``, ``"budget_transfer"``).
        """
        ...

    async def detect(self) -> Sequence[Coalition]:
        """Run coalition detection over recorded observations.

        Returns all currently active coalitions above the confidence
        threshold.
        """
        ...

    async def disband(self, coalition_id: str) -> None:
        """Remove a coalition record (e.g., after operator review).

        No-op when ``coalition_id`` does not exist.
        """
        ...


@runtime_checkable
class GovernancePolicy(Protocol):
    """High-level governance coordinator.

    Takes evidence about a principal and returns the appropriate action.
    """

    async def evaluate(self, evidence: GovernanceEvidence) -> GovernanceDecision:
        """Produce a :class:`GovernanceDecision` for ``evidence.principal_fqn``.

        Implementations must be deterministic given the same evidence (no
        randomness) so decisions can be logged and audited.
        """
        ...

    async def score_risk(self, principal_fqn: str) -> RiskScore:
        """Compute the current risk score for ``principal_fqn``.

        Returns a zero-risk score (score=0.0) for unknown principals.
        """
        ...
