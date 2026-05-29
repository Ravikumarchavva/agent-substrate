"""Trust-aware feed ranker bridge — Section 10 integration helper.

Bridges the :class:`InMemoryFeedRanker` with the live :class:`TrustGraph`
and optionally an :class:`EconomicSignalSource` so callers don't need to
manually populate trust scores or apply budget-exhaustion penalties.

Usage::

    ranker = TrustAwareFeedRanker(trust_graph=graph, ledger=budget_ledger)
    result = await ranker.rank_with_context(
        candidates=[
            RawCandidate(candidate_id="msg-1", source_principal_fqn="agent:foo", relevance=0.9),
        ],
        strategy=ScoringStrategy.PRODUCT,
    )
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ravi.platform.ranking._in_memory import InMemoryFeedRanker
from ravi.kernel.economic._signals import EconomicSignalKind, EconomicSignalSource
from ravi.platform.ranking._contracts import (
    FeedRequest,
    FeedResult,
    RankingCandidate,
    ScoringStrategy,
)
from ravi.kernel.runtime._identity import PrincipalId

__all__ = ["RawCandidate", "TrustAwareFeedRanker"]

_DEFAULT_TRUST_SCORE = 0.3
_BUDGET_PENALTY_FACTOR = 0.5


@dataclass(frozen=True, slots=True)
class RawCandidate:
    """Input shape before trust-score enrichment.

    Parameters
    ----------
    candidate_id:
        Stable identifier for this item (e.g. message_id, chunk_id).
    source_principal_fqn:
        Fully-qualified name of the agent/user that produced this item.
    relevance:
        Caller-supplied relevance score in [0, 1].
    content_hash:
        Optional SHA-256 hex digest for sybil detection.
    """

    candidate_id: str
    source_principal_fqn: str
    relevance: float
    content_hash: str | None = None


class TrustAwareFeedRanker:
    """Wraps :class:`InMemoryFeedRanker` with live trust and budget signals.

    Trust scores are fetched from the :class:`TrustGraph` for each unique
    principal before building :class:`RankingCandidate` objects.  When an
    :class:`EconomicSignalSource` is provided, principals with an active
    ``BUDGET_EXHAUSTED`` signal receive a penalty multiplied into their
    resolved trust score.

    Parameters
    ----------
    trust_graph:
        Live trust graph — queried once per unique principal per ``rank_with_context`` call.
    signal_source:
        Optional economic signal source.  When ``None``, no budget penalty is applied.
    default_trust_score:
        Trust score assigned when the principal has no entry in the graph.
    budget_penalty_factor:
        Multiplier applied to the trust score of principals with an active
        ``BUDGET_EXHAUSTED`` signal.  Default ``0.5`` halves their trust.
    """

    def __init__(
        self,
        *,
        trust_graph: object,
        signal_source: EconomicSignalSource | None = None,
        default_trust_score: float = _DEFAULT_TRUST_SCORE,
        budget_penalty_factor: float = _BUDGET_PENALTY_FACTOR,
    ) -> None:
        if not 0.0 <= default_trust_score <= 1.0:
            raise ValueError("default_trust_score must be in [0, 1]")
        if not 0.0 < budget_penalty_factor <= 1.0:
            raise ValueError("budget_penalty_factor must be in (0, 1]")
        self._trust_graph = trust_graph
        self._signal_source = signal_source
        self._default_trust = default_trust_score
        self._budget_penalty = budget_penalty_factor
        self._ranker = InMemoryFeedRanker()

    async def rank_with_context(
        self,
        candidates: Sequence[RawCandidate],
        *,
        strategy: ScoringStrategy = ScoringStrategy.PRODUCT,
        top_k: int | None = None,
        attention_threshold: float | None = None,
        trust_blend_alpha: float = 0.7,
        trust_gate_min: float = 0.3,
        sybil_max_share: float | None = None,
    ) -> FeedResult:
        """Rank ``candidates`` after fetching live trust scores.

        Steps:
        1. Collect unique principals and fetch trust scores from ``trust_graph``.
        2. Optionally apply budget-exhaustion penalty from ``signal_source``.
        3. Build :class:`RankingCandidate` objects with enriched trust scores.
        4. Delegate to :class:`InMemoryFeedRanker`.
        """
        if not candidates:
            raise ValueError("candidates must be non-empty")

        unique_fqns = {c.source_principal_fqn for c in candidates}

        # Fetch trust scores for all unique principals.
        trust_scores: dict[str, float] = {}
        for fqn in unique_fqns:
            score = await self._fetch_trust(fqn)
            trust_scores[fqn] = score

        # Apply budget-exhaustion penalty if a signal source is configured.
        if self._signal_source is not None:
            for fqn in unique_fqns:
                principal = PrincipalId(
                    kind="agent",  # type: ignore[arg-type]
                    tenant_id="",
                    workspace_id="",
                    name=fqn,
                )
                try:
                    signals = await self._signal_source.signals_for(principal)
                except Exception:  # noqa: BLE001
                    signals = ()
                if any(
                    s.signal_type is EconomicSignalKind.BUDGET_EXHAUSTED
                    for s in signals
                ):
                    trust_scores[fqn] = max(
                        0.0, trust_scores[fqn] * self._budget_penalty
                    )

        # Build RankingCandidate objects with enriched trust scores.
        enriched: list[RankingCandidate] = [
            RankingCandidate(
                candidate_id=c.candidate_id,
                source_principal_fqn=c.source_principal_fqn,
                relevance=c.relevance,
                trust_score=trust_scores[c.source_principal_fqn],
                content_hash=c.content_hash,
            )
            for c in candidates
        ]

        request = FeedRequest(
            candidates=enriched,
            strategy=strategy,
            top_k=top_k,
            attention_threshold=attention_threshold,
            trust_blend_alpha=trust_blend_alpha,
            trust_gate_min=trust_gate_min,
            sybil_max_share=sybil_max_share,
        )
        return await self._ranker.rank(request)

    async def _fetch_trust(self, principal_fqn: str) -> float:
        """Fetch trust score for ``principal_fqn`` from the graph."""
        score_for = getattr(self._trust_graph, "score_for", None)
        if score_for is None:
            return self._default_trust
        try:
            result = await score_for(
                PrincipalId(
                    kind="agent",  # type: ignore[arg-type]
                    tenant_id="",
                    workspace_id="",
                    name=principal_fqn,
                )
            )
        except Exception:  # noqa: BLE001
            return self._default_trust
        if result is None:
            return self._default_trust
        return float(getattr(result, "value", self._default_trust))
