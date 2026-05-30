"""Tests for Section 10 — Ranking + Attention.

Coverage
--------
- Kernel contracts: RankingCandidate, AttentionWeight, FeedRequest, FeedResult
- InMemoryFeedRanker: PRODUCT, WEIGHTED_SUM, TRUST_GATE strategies
- Top-k filtering, attention threshold filtering
- Sybil suppression
- FeedRanker + RankingPolicy protocol conformance
- Error paths: empty candidates, out-of-range scores
"""

from __future__ import annotations

import pytest

from ravi.platform.ranking import InMemoryFeedRanker
from ravi.platform.ranking import (
    FeedRanker,
    FeedRequest,
    RankingCandidate,
    RankingPolicy,
    ScoringStrategy,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _candidate(
    cid: str,
    *,
    relevance: float = 0.8,
    trust: float = 0.9,
    principal: str = "agent/t/ws/alice",
    content_hash: str | None = None,
) -> RankingCandidate:
    return RankingCandidate(
        candidate_id=cid,
        source_principal_fqn=principal,
        relevance=relevance,
        trust_score=trust,
        content_hash=content_hash,
    )


def _request(
    candidates: list[RankingCandidate],
    **kwargs,
) -> FeedRequest:
    return FeedRequest(candidates=candidates, **kwargs)


# ===========================================================================
# Protocol conformance
# ===========================================================================


class TestProtocolConformance:
    def test_satisfies_feed_ranker_protocol(self) -> None:
        assert isinstance(InMemoryFeedRanker(), FeedRanker)

    def test_satisfies_ranking_policy_protocol(self) -> None:
        assert isinstance(InMemoryFeedRanker(), RankingPolicy)


# ===========================================================================
# Scoring strategies — score() method
# ===========================================================================


class TestScoringPolicy:
    def test_product_strategy(self) -> None:
        ranker = InMemoryFeedRanker()
        c = _candidate("x", relevance=0.6, trust=0.5)
        s = ranker.score(
            c,
            strategy=ScoringStrategy.PRODUCT,
            trust_blend_alpha=0.7,
            trust_gate_min=0.3,
        )
        assert s == pytest.approx(0.3)

    def test_weighted_sum_strategy(self) -> None:
        ranker = InMemoryFeedRanker()
        c = _candidate("x", relevance=0.8, trust=0.4)
        # 0.7*0.8 + 0.3*0.4 = 0.56 + 0.12 = 0.68
        s = ranker.score(
            c,
            strategy=ScoringStrategy.WEIGHTED_SUM,
            trust_blend_alpha=0.7,
            trust_gate_min=0.3,
        )
        assert s == pytest.approx(0.68)

    def test_trust_gate_excludes_below_min(self) -> None:
        ranker = InMemoryFeedRanker()
        c = _candidate("x", relevance=0.9, trust=0.2)
        s = ranker.score(
            c,
            strategy=ScoringStrategy.TRUST_GATE,
            trust_blend_alpha=0.7,
            trust_gate_min=0.3,
        )
        assert s is None

    def test_trust_gate_passes_at_threshold(self) -> None:
        ranker = InMemoryFeedRanker()
        c = _candidate("x", relevance=1.0, trust=0.3)
        s = ranker.score(
            c,
            strategy=ScoringStrategy.TRUST_GATE,
            trust_blend_alpha=0.7,
            trust_gate_min=0.3,
        )
        assert s == pytest.approx(0.3)


# ===========================================================================
# Ranking — basic
# ===========================================================================


class TestRankBasic:
    async def test_single_candidate_gets_attention_one(self) -> None:
        ranker = InMemoryFeedRanker()
        result = await ranker.rank(_request([_candidate("a")]))
        assert len(result.ranked) == 1
        assert result.ranked[0].attention == pytest.approx(1.0)

    async def test_candidates_sorted_by_descending_attention(self) -> None:
        ranker = InMemoryFeedRanker()
        candidates = [
            _candidate("low", relevance=0.2, trust=0.3),
            _candidate("high", relevance=0.9, trust=0.9),
            _candidate("mid", relevance=0.5, trust=0.6),
        ]
        result = await ranker.rank(_request(candidates))
        ids = [r.candidate_id for r in result.ranked]
        assert ids[0] == "high"
        assert ids[-1] == "low"

    async def test_attention_weights_sum_to_one(self) -> None:
        ranker = InMemoryFeedRanker()
        candidates = [_candidate(f"c{i}", relevance=i / 10, trust=0.8) for i in range(1, 6)]
        result = await ranker.rank(_request(candidates))
        total = sum(r.attention for r in result.ranked)
        assert total == pytest.approx(1.0, abs=1e-9)

    async def test_empty_candidates_raises(self) -> None:
        ranker = InMemoryFeedRanker()
        with pytest.raises(ValueError, match="non-empty"):
            await ranker.rank(_request([]))

    async def test_relevance_out_of_range_raises(self) -> None:
        ranker = InMemoryFeedRanker()
        with pytest.raises(ValueError, match="relevance"):
            await ranker.rank(_request([_candidate("x", relevance=1.5)]))

    async def test_trust_out_of_range_raises(self) -> None:
        ranker = InMemoryFeedRanker()
        with pytest.raises(ValueError, match="trust_score"):
            await ranker.rank(_request([_candidate("x", trust=-0.1)]))

    async def test_rank_field_is_one_based(self) -> None:
        ranker = InMemoryFeedRanker()
        result = await ranker.rank(_request([_candidate("a"), _candidate("b")]))
        ranks = {r.candidate_id: r.rank for r in result.ranked}
        assert 1 in ranks.values()
        assert 2 in ranks.values()


# ===========================================================================
# Strategies via rank()
# ===========================================================================


class TestRankStrategies:
    async def test_trust_gate_excludes_and_counts(self) -> None:
        ranker = InMemoryFeedRanker()
        candidates = [
            _candidate("pass", relevance=0.8, trust=0.8),
            _candidate("fail", relevance=0.9, trust=0.1),
        ]
        result = await ranker.rank(
            _request(candidates, strategy=ScoringStrategy.TRUST_GATE, trust_gate_min=0.3)
        )
        assert result.excluded_count == 1
        assert len(result.ranked) == 1
        assert result.ranked[0].candidate_id == "pass"

    async def test_trust_gate_all_excluded_returns_empty(self) -> None:
        ranker = InMemoryFeedRanker()
        candidates = [
            _candidate("a", trust=0.1),
            _candidate("b", trust=0.2),
        ]
        result = await ranker.rank(
            _request(candidates, strategy=ScoringStrategy.TRUST_GATE, trust_gate_min=0.5)
        )
        assert result.excluded_count == 2
        assert result.ranked == [] or list(result.ranked) == []


# ===========================================================================
# Top-k and attention threshold
# ===========================================================================


class TestTopKAndThreshold:
    async def test_top_k_limits_results(self) -> None:
        ranker = InMemoryFeedRanker()
        candidates = [_candidate(f"c{i}", relevance=i / 10, trust=0.9) for i in range(1, 8)]
        result = await ranker.rank(_request(candidates, top_k=3))
        assert len(result.ranked) == 3

    async def test_top_k_excess_counted_as_excluded(self) -> None:
        ranker = InMemoryFeedRanker()
        candidates = [_candidate(f"c{i}") for i in range(5)]
        result = await ranker.rank(_request(candidates, top_k=2))
        assert result.excluded_count == 3

    async def test_attention_threshold_filters_low_weight(self) -> None:
        ranker = InMemoryFeedRanker()
        candidates = [
            _candidate("strong", relevance=0.9, trust=0.9),
            _candidate("weak", relevance=0.01, trust=0.01),
        ]
        result = await ranker.rank(_request(candidates, attention_threshold=0.1))
        ids = [r.candidate_id for r in result.ranked]
        assert "strong" in ids
        assert "weak" not in ids


# ===========================================================================
# Sybil suppression
# ===========================================================================


class TestSybilSuppression:
    async def test_sybil_penalty_applied_when_share_exceeded(self) -> None:
        ranker = InMemoryFeedRanker()
        # Principal 'bot' has 4 candidates, 'human' has 1.
        # Without suppression, bot would dominate.
        candidates = [
            _candidate(f"bot-{i}", principal="bot", relevance=0.8, trust=0.8)
            for i in range(4)
        ] + [_candidate("human-1", principal="human", relevance=0.8, trust=0.8)]

        result = await ranker.rank(_request(candidates, sybil_max_share=0.3))
        assert result.sybil_suppressed_count > 0
        penalised = [r for r in result.ranked if r.sybil_penalised]
        assert len(penalised) > 0

    async def test_no_sybil_when_share_not_exceeded(self) -> None:
        ranker = InMemoryFeedRanker()
        candidates = [
            _candidate("a", principal="p1"),
            _candidate("b", principal="p2"),
        ]
        result = await ranker.rank(_request(candidates, sybil_max_share=0.6))
        assert result.sybil_suppressed_count == 0
        assert all(not r.sybil_penalised for r in result.ranked)

    async def test_no_sybil_suppression_when_not_configured(self) -> None:
        ranker = InMemoryFeedRanker()
        candidates = [_candidate(f"bot-{i}", principal="bot") for i in range(5)]
        result = await ranker.rank(_request(candidates))
        assert result.sybil_suppressed_count == 0

    async def test_attention_sums_to_one_after_sybil(self) -> None:
        ranker = InMemoryFeedRanker()
        candidates = [
            _candidate(f"bot-{i}", principal="bot", relevance=0.8, trust=0.8)
            for i in range(3)
        ] + [_candidate("human", principal="human")]
        result = await ranker.rank(_request(candidates, sybil_max_share=0.2))
        total = sum(r.attention for r in result.ranked)
        assert total == pytest.approx(1.0, abs=1e-9)
