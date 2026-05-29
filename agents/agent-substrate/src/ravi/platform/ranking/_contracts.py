"""Ranking + Attention kernel contracts — Section 10.

The ranking plane answers: *given a set of candidate messages or agent
activations, in what order should they be processed, and how much attention
should the reader devote to each?*

Key concepts
------------
``RankingCandidate``
    A unit of content (a message, a tool result, an agent output, a memory
    chunk) with a trust-weighted signal.  The trust score comes from the
    trust graph (S6); the relevance score is provided by the caller (e.g.,
    from a vector similarity search).

``AttentionWeight``
    How much relative attention the reader should allocate to a candidate
    (0 = ignore, 1 = maximum focus).  The ``RankingPolicy`` computes this
    from relevance × trust, then normalises the whole candidate set so
    weights sum to 1.

``SybilSuppression``
    Detects when many candidates originate from the same principal (sybil
    amplification) and applies a penalty so no single agent can dominate the
    attention window.

``FeedRequest / FeedResult``
    High-level API: the ``FeedRanker`` takes a ``FeedRequest`` (a list of
    candidates + context) and returns a ``FeedResult`` (ranked list with
    attention weights, optionally pruned by a ``top_k`` or
    ``attention_threshold``).

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
    "ScoringStrategy",
    "RankingCandidate",
    "AttentionWeight",
    "FeedRequest",
    "FeedResult",
    "RankingPolicy",
    "FeedRanker",
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ScoringStrategy(Enum):
    """How relevance and trust are combined into a final score."""

    PRODUCT = auto()
    """score = relevance × trust (default; penalises low-trust heavily)."""

    WEIGHTED_SUM = auto()
    """score = α·relevance + (1-α)·trust (configurable blend)."""

    TRUST_GATE = auto()
    """Candidates with trust < threshold are excluded before scoring."""


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RankingCandidate:
    """A single item to be ranked.

    Parameters
    ----------
    candidate_id:
        Stable identifier (e.g., message_id, chunk_id).
    source_principal_fqn:
        Fully-qualified name of the agent or user that produced this item.
    relevance:
        Caller-supplied relevance score in [0, 1].
    trust_score:
        Trust graph score in [0, 1] for ``source_principal_fqn``.
        Pass 1.0 for human/system messages where trust is assumed full.
    content_hash:
        Optional SHA-256 hex digest for de-duplication and sybil detection.
    """

    candidate_id: str
    source_principal_fqn: str
    relevance: float
    trust_score: float
    content_hash: str | None = None


@dataclass(frozen=True, slots=True)
class AttentionWeight:
    """Normalised attention allocation for a ranked candidate."""

    candidate_id: str
    rank: int
    """1-based rank (1 = highest priority)."""
    composite_score: float
    """Pre-normalisation combined score."""
    attention: float
    """Normalised weight in [0, 1]; sums to 1 across the full ranked set."""
    sybil_penalised: bool = False
    """``True`` when the sybil suppressor reduced this candidate's score."""


@dataclass(frozen=True, slots=True)
class FeedRequest:
    """Input to the feed ranker.

    Parameters
    ----------
    candidates:
        Items to be ranked.  Must be non-empty.
    strategy:
        Scoring strategy to apply.
    top_k:
        If set, return only the top-k candidates.
    attention_threshold:
        If set, discard candidates whose final attention < threshold.
    trust_blend_alpha:
        Only used when ``strategy == WEIGHTED_SUM``.  Controls the
        relevance weight.  ``alpha=0.7`` means 70% relevance, 30% trust.
    trust_gate_min:
        Only used when ``strategy == TRUST_GATE``.  Candidates with
        ``trust_score < trust_gate_min`` are excluded entirely.
    sybil_max_share:
        Maximum fraction of the total attention any single principal may
        hold.  ``None`` = no sybil suppression.
    """

    candidates: Sequence[RankingCandidate]
    strategy: ScoringStrategy = ScoringStrategy.PRODUCT
    top_k: int | None = None
    attention_threshold: float | None = None
    trust_blend_alpha: float = 0.7
    trust_gate_min: float = 0.3
    sybil_max_share: float | None = None


@dataclass(frozen=True, slots=True)
class FeedResult:
    """Output from :meth:`FeedRanker.rank`."""

    ranked: Sequence[AttentionWeight]
    """Candidates in descending rank order, with attention weights."""
    excluded_count: int = 0
    """Number of candidates excluded by trust gate or attention threshold."""
    sybil_suppressed_count: int = 0
    """Number of candidates penalised by sybil suppression."""


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class RankingPolicy(Protocol):
    """Computes a composite score for a single candidate.

    Implementations must be pure (no I/O, no side effects) and return a
    value in [0, 1].
    """

    def score(
        self,
        candidate: RankingCandidate,
        *,
        strategy: ScoringStrategy,
        trust_blend_alpha: float,
        trust_gate_min: float,
    ) -> float | None:
        """Return composite score, or ``None`` to exclude the candidate.

        ``None`` is returned when ``strategy == TRUST_GATE`` and the
        candidate's trust is below ``trust_gate_min``.
        """
        ...


@runtime_checkable
class FeedRanker(Protocol):
    """High-level feed generator — ranks a set of candidates by relevance
    and trust, applies sybil suppression, and returns attention weights.
    """

    async def rank(self, request: FeedRequest) -> FeedResult:
        """Rank ``request.candidates`` and return weighted results.

        Raises :class:`ValueError` when ``candidates`` is empty or contains
        scores outside [0, 1].
        """
        ...
