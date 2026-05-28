"""In-process feed ranker — reference implementation of Section 10.

Implements both :class:`RankingPolicy` and :class:`FeedRanker` in a single
stateless class.  No I/O, no external dependencies — pure Python 3.13+.

Algorithm
---------
1. **Score each candidate** using the requested :class:`ScoringStrategy`.
   TRUST_GATE first filters out below-threshold candidates; PRODUCT
   multiplies relevance × trust; WEIGHTED_SUM blends them.

2. **Sybil suppression** (optional): group candidates by
   ``source_principal_fqn``; if any principal's raw attention share exceeds
   ``sybil_max_share``, proportionally reduce their scores and redistribute
   the excess to other principals.

3. **Normalise** composite scores to produce attention weights that sum to 1.

4. **Rank** by descending attention; apply ``top_k`` and
   ``attention_threshold`` filters.

Thread-safety
~~~~~~~~~~~~~
The class is stateless — all logic lives in :meth:`rank`.  Multiple
concurrent callers are safe without any locking.
"""

from __future__ import annotations

from ravi.platform.ranking._contracts import (
    AttentionWeight,
    FeedRequest,
    FeedResult,
    RankingCandidate,
    ScoringStrategy,
)

__all__ = ["InMemoryFeedRanker"]


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


class InMemoryFeedRanker:
    """Stateless in-process :class:`FeedRanker` + :class:`RankingPolicy`."""

    # ------------------------------------------------------------------
    # RankingPolicy
    # ------------------------------------------------------------------

    def score(
        self,
        candidate: RankingCandidate,
        *,
        strategy: ScoringStrategy,
        trust_blend_alpha: float,
        trust_gate_min: float,
    ) -> float | None:
        rel = _clamp01(candidate.relevance)
        trust = _clamp01(candidate.trust_score)

        if strategy is ScoringStrategy.TRUST_GATE:
            if trust < trust_gate_min:
                return None
            return rel * trust

        if strategy is ScoringStrategy.PRODUCT:
            return rel * trust

        if strategy is ScoringStrategy.WEIGHTED_SUM:
            alpha = _clamp01(trust_blend_alpha)
            return alpha * rel + (1.0 - alpha) * trust

        raise ValueError(f"Unknown ScoringStrategy: {strategy!r}")  # pragma: no cover

    # ------------------------------------------------------------------
    # FeedRanker
    # ------------------------------------------------------------------

    async def rank(self, request: FeedRequest) -> FeedResult:
        if not request.candidates:
            raise ValueError("FeedRequest.candidates must be non-empty")

        # Validate score ranges
        for c in request.candidates:
            if not (0.0 <= c.relevance <= 1.0):
                raise ValueError(
                    f"Candidate {c.candidate_id!r}: relevance {c.relevance}"
                    " is outside [0, 1]"
                )
            if not (0.0 <= c.trust_score <= 1.0):
                raise ValueError(
                    f"Candidate {c.candidate_id!r}: trust_score {c.trust_score}"
                    " is outside [0, 1]"
                )

        # --- 1. Score each candidate ---
        scored: list[tuple[RankingCandidate, float]] = []
        excluded = 0
        for c in request.candidates:
            s = self.score(
                c,
                strategy=request.strategy,
                trust_blend_alpha=request.trust_blend_alpha,
                trust_gate_min=request.trust_gate_min,
            )
            if s is None:
                excluded += 1
            else:
                scored.append((c, s))

        if not scored:
            return FeedResult(ranked=[], excluded_count=excluded)

        # --- 2. Sybil suppression ---
        sybil_penalised: set[str] = set()
        if request.sybil_max_share is not None:
            scored, sybil_penalised = _apply_sybil_suppression(
                scored, max_share=request.sybil_max_share
            )

        # --- 3. Normalise ---
        total = sum(s for _, s in scored)
        if total == 0.0:
            weights = [0.0] * len(scored)
        else:
            weights = [s / total for _, s in scored]

        # --- 4. Sort by descending attention ---
        ranked_pairs = sorted(
            zip([c for c, _ in scored], weights, [s for _, s in scored]),
            key=lambda t: t[1],
            reverse=True,
        )

        # --- 5. Build result ---
        results: list[AttentionWeight] = []
        for rank_idx, (c, attention, composite) in enumerate(ranked_pairs, start=1):
            if request.attention_threshold is not None and attention < request.attention_threshold:
                excluded += 1
                continue
            results.append(
                AttentionWeight(
                    candidate_id=c.candidate_id,
                    rank=rank_idx,
                    composite_score=composite,
                    attention=attention,
                    sybil_penalised=c.candidate_id in sybil_penalised,
                )
            )

        if request.top_k is not None:
            excluded += max(0, len(results) - request.top_k)
            results = results[: request.top_k]

        return FeedResult(
            ranked=results,
            excluded_count=excluded,
            sybil_suppressed_count=len(sybil_penalised),
        )


def _apply_sybil_suppression(
    scored: list[tuple[RankingCandidate, float]],
    *,
    max_share: float,
) -> tuple[list[tuple[RankingCandidate, float]], set[str]]:
    """Proportionally reduce any principal whose share exceeds ``max_share``.

    Surplus score is redistributed uniformly to all other principals.
    Returns the adjusted scored list and the set of penalised candidate IDs.
    """
    total = sum(s for _, s in scored)
    if total == 0.0:
        return scored, set()

    # Compute per-principal total share
    principal_totals: dict[str, float] = {}
    for c, s in scored:
        principal_totals[c.source_principal_fqn] = (
            principal_totals.get(c.source_principal_fqn, 0.0) + s
        )

    penalised_principals: set[str] = set()
    for p, p_total in principal_totals.items():
        if p_total / total > max_share:
            penalised_principals.add(p)

    if not penalised_principals:
        return scored, set()

    penalised_cids: set[str] = set()
    adjusted: list[tuple[RankingCandidate, float]] = []
    for c, s in scored:
        if c.source_principal_fqn in penalised_principals:
            p_total = principal_totals[c.source_principal_fqn]
            # Scale down so the principal's share equals exactly max_share
            new_s = s * (max_share * total) / p_total
            adjusted.append((c, new_s))
            penalised_cids.add(c.candidate_id)
        else:
            adjusted.append((c, s))

    return adjusted, penalised_cids
