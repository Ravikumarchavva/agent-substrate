"""In-process governance implementation — Section 11 reference impl.

Provides:
- ``InMemoryQuarantineActuator``: tracks quarantined principals in a set.
- ``InMemoryCoalitionDetector``: simple co-occurrence detector — when two
  principals exchange ``trust_vote_up`` events ≥ ``min_mutual_votes`` times
  in either direction, they are flagged as ``TRUST_INFLATION``.  For timing
  correlation, when any two principals send events within ``timing_window_s``
  seconds of each other ≥ ``min_correlated_events`` times, they are flagged
  as ``TIMING_CORRELATED``.
- ``InMemoryGovernancePolicy``: evaluates risk from (violation count +
  coalition membership) and maps to a ``GovernanceAction`` via configurable
  thresholds.

Thread-safety
~~~~~~~~~~~~~
All shared state is guarded by ``threading.RLock``.  No lock held across
``await``.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from datetime import datetime, timezone
from typing import Sequence

from ravi.kernel.governance._contracts import (
    Coalition,
    CoalitionKind,
    GovernanceAction,
    GovernanceDecision,
    GovernanceEvidence,
    RiskScore,
)

__all__ = [
    "InMemoryCoalitionDetector",
    "InMemoryGovernancePolicy",
    "InMemoryQuarantineActuator",
]

UTC = timezone.utc


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# QuarantineActuator
# ---------------------------------------------------------------------------


class InMemoryQuarantineActuator:
    """In-process quarantine actuator backed by a set."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._quarantined: set[str] = set()

    async def quarantine(self, principal_fqn: str, reason: str) -> None:  # noqa: ARG002
        with self._lock:
            self._quarantined.add(principal_fqn)

    async def lift_quarantine(self, principal_fqn: str) -> None:
        with self._lock:
            if principal_fqn not in self._quarantined:
                raise KeyError(f"{principal_fqn!r} is not quarantined")
            self._quarantined.discard(principal_fqn)

    async def is_quarantined(self, principal_fqn: str) -> bool:
        with self._lock:
            return principal_fqn in self._quarantined


# ---------------------------------------------------------------------------
# CoalitionDetector
# ---------------------------------------------------------------------------


class InMemoryCoalitionDetector:
    """Simple pairwise coalition detector.

    Parameters
    ----------
    min_mutual_votes:
        Minimum number of ``trust_vote_up`` events between two principals
        (in either direction) before a TRUST_INFLATION coalition is flagged.
    timing_window_s:
        Window in seconds for timing correlation.
    min_correlated_events:
        Minimum number of events within ``timing_window_s`` of each other
        before a TIMING_CORRELATED coalition is flagged.
    confidence_floor:
        Minimum confidence for a coalition to appear in :meth:`detect`.
    """

    def __init__(
        self,
        *,
        min_mutual_votes: int = 3,
        timing_window_s: float = 2.0,
        min_correlated_events: int = 3,
        confidence_floor: float = 0.5,
    ) -> None:
        self._min_votes = min_mutual_votes
        self._timing_window = timing_window_s
        self._min_corr = min_correlated_events
        self._conf_floor = confidence_floor
        self._lock = threading.RLock()
        # (a, b) → count of trust_vote_up events (sorted pair)
        self._vote_matrix: dict[tuple[str, str], int] = defaultdict(int)
        # All events as (principal_fqn, timestamp ISO)
        self._events: list[tuple[str, str, str]] = []  # (principal, event_type, ts)
        # Known coalitions: coalition_id → Coalition
        self._coalitions: dict[str, Coalition] = {}

    async def observe(
        self,
        principal_fqn: str,
        event_type: str,
        *,
        counterparty_fqn: str | None = None,
        timestamp_utc: str,
    ) -> None:
        with self._lock:
            self._events.append((principal_fqn, event_type, timestamp_utc))
            if event_type == "trust_vote_up" and counterparty_fqn:
                pair = tuple(sorted((principal_fqn, counterparty_fqn)))
                self._vote_matrix[pair] += 1  # type: ignore[index]

    async def detect(self) -> Sequence[Coalition]:
        with self._lock:
            active: list[Coalition] = []

            # Trust inflation detection
            for (a, b), count in self._vote_matrix.items():
                if count >= self._min_votes:
                    cid = f"coa-trust-{a}-{b}"
                    confidence = min(1.0, count / (self._min_votes * 2))
                    if confidence >= self._conf_floor:
                        c = Coalition(
                            coalition_id=cid,
                            member_fqns=(a, b),
                            kind=CoalitionKind.TRUST_INFLATION,
                            confidence=confidence,
                            detected_at=_iso_now(),
                            evidence_summary=(
                                f"{count} mutual trust votes (threshold={self._min_votes})"
                            ),
                        )
                        self._coalitions[cid] = c
                        active.append(c)

            # Timing correlation detection
            parsed: list[tuple[str, datetime]] = []
            for fqn, _etype, ts in self._events:
                try:
                    parsed.append((fqn, datetime.fromisoformat(ts)))
                except ValueError:
                    continue

            parsed.sort(key=lambda x: x[1])
            # Count pairwise close events
            pair_close: dict[tuple[str, str], int] = defaultdict(int)
            for i, (fqn_i, ts_i) in enumerate(parsed):
                for j in range(i + 1, len(parsed)):
                    fqn_j, ts_j = parsed[j]
                    if fqn_i == fqn_j:
                        continue
                    delta = (ts_j - ts_i).total_seconds()
                    if delta > self._timing_window:
                        break
                    pair = tuple(sorted((fqn_i, fqn_j)))
                    pair_close[pair] += 1  # type: ignore[index]

            for (a, b), count in pair_close.items():
                if count >= self._min_corr:
                    cid = f"coa-timing-{a}-{b}"
                    confidence = min(1.0, count / (self._min_corr * 2))
                    if confidence >= self._conf_floor:
                        c = Coalition(
                            coalition_id=cid,
                            member_fqns=(a, b),
                            kind=CoalitionKind.TIMING_CORRELATED,
                            confidence=confidence,
                            detected_at=_iso_now(),
                            evidence_summary=(
                                f"{count} events within {self._timing_window}s"
                                f" (threshold={self._min_corr})"
                            ),
                        )
                        self._coalitions[cid] = c
                        active.append(c)

            return active

    async def disband(self, coalition_id: str) -> None:
        with self._lock:
            self._coalitions.pop(coalition_id, None)


# ---------------------------------------------------------------------------
# GovernancePolicy
# ---------------------------------------------------------------------------


class InMemoryGovernancePolicy:
    """Threshold-based governance policy.

    Parameters
    ----------
    warn_threshold:
        Risk score above which WARN is issued.
    throttle_threshold:
        Risk score above which THROTTLE is issued.
    quarantine_threshold:
        Risk score above which QUARANTINE is issued.
    coalition_risk_bump:
        Additional risk added per active coalition a principal belongs to.
    violation_risk_per_count:
        Risk added per recent violation.
    """

    def __init__(
        self,
        *,
        warn_threshold: float = 0.3,
        throttle_threshold: float = 0.6,
        quarantine_threshold: float = 0.85,
        coalition_risk_bump: float = 0.2,
        violation_risk_per_count: float = 0.1,
    ) -> None:
        self._warn = warn_threshold
        self._throttle = throttle_threshold
        self._quarantine = quarantine_threshold
        self._coalition_bump = coalition_risk_bump
        self._violation_per = violation_risk_per_count

    async def evaluate(self, evidence: GovernanceEvidence) -> GovernanceDecision:
        risk = evidence.risk_score.score
        # Bump for violations
        risk = min(1.0, risk + evidence.recent_violation_count * self._violation_per)
        # Bump for coalitions
        risk = min(1.0, risk + len(evidence.active_coalitions) * self._coalition_bump)

        coalition_id = (
            evidence.active_coalitions[0].coalition_id
            if evidence.active_coalitions
            else None
        )

        if risk >= self._quarantine:
            action = GovernanceAction.QUARANTINE
            rationale = f"risk={risk:.2f} ≥ quarantine threshold={self._quarantine}"
        elif risk >= self._throttle:
            action = GovernanceAction.THROTTLE
            rationale = f"risk={risk:.2f} ≥ throttle threshold={self._throttle}"
        elif risk >= self._warn:
            action = GovernanceAction.WARN
            rationale = f"risk={risk:.2f} ≥ warn threshold={self._warn}"
        else:
            action = GovernanceAction.ALLOW
            rationale = f"risk={risk:.2f} below all thresholds"

        return GovernanceDecision(
            principal_fqn=evidence.principal_fqn,
            action=action,
            rationale=rationale,
            decided_at=_iso_now(),
            coalition_id=coalition_id,
        )

    async def score_risk(self, principal_fqn: str) -> RiskScore:  # noqa: ARG002
        return RiskScore(
            principal_fqn=principal_fqn,
            score=0.0,
            contributors=(),
            scored_at=_iso_now(),
        )
