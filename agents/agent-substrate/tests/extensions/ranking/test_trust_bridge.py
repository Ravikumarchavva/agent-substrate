"""Section 10 — TrustAwareFeedRanker end-to-end tests.

Verifies that the bridge correctly:
- fetches trust scores from a live InMemoryTrustGraph
- applies budget-exhaustion penalties from InMemoryBudgetLedger signals
- delegates ranking to InMemoryFeedRanker
- produces ranked FeedResult with attention weights
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ravi.extensions.economic._in_memory import InMemoryBudgetLedger
from ravi.extensions.ranking._trust_bridge import RawCandidate, TrustAwareFeedRanker
from ravi.extensions.trust._in_memory import InMemoryTrustGraph
from ravi.kernel.contracts._coordination import TrustSignal
from ravi.kernel.economic._signals import EconomicSignalKind
from ravi.kernel.ranking._contracts import FeedResult, ScoringStrategy
from ravi.kernel.runtime._identity import PrincipalId, PrincipalKind


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _set_trust(graph: InMemoryTrustGraph, principal: PrincipalId, value: float) -> None:
    """Helper: ingest a single trust signal so score_for returns ~value."""
    await graph.ingest(
        principal,
        TrustSignal(
            signal_type="test_signal",
            value=value,
            source_id="test",
            issued_at=_now(),
        ),
    )


def _principal(name: str, tenant: str = "t1") -> PrincipalId:
    return PrincipalId(
        kind=PrincipalKind.AGENT,
        tenant_id=tenant,
        workspace_id="w1",
        name=name,
    )


def _raw(candidate_id: str, fqn: str, relevance: float = 0.8) -> RawCandidate:
    return RawCandidate(
        candidate_id=candidate_id,
        source_principal_fqn=fqn,
        relevance=relevance,
    )


# ---------------------------------------------------------------------------
# TrustAwareFeedRanker construction
# ---------------------------------------------------------------------------


def test_construction_defaults() -> None:
    graph = InMemoryTrustGraph()
    ranker = TrustAwareFeedRanker(trust_graph=graph)
    assert ranker is not None


def test_construction_invalid_default_trust() -> None:
    graph = InMemoryTrustGraph()
    with pytest.raises(ValueError, match="default_trust_score"):
        TrustAwareFeedRanker(trust_graph=graph, default_trust_score=1.5)


def test_construction_invalid_penalty() -> None:
    graph = InMemoryTrustGraph()
    with pytest.raises(ValueError, match="budget_penalty_factor"):
        TrustAwareFeedRanker(trust_graph=graph, budget_penalty_factor=0.0)


# ---------------------------------------------------------------------------
# rank_with_context — trust enrichment
# ---------------------------------------------------------------------------


async def test_rank_uses_trust_graph_scores() -> None:
    graph = InMemoryTrustGraph()
    high = _principal("high-trust")
    low = _principal("low-trust")
    await _set_trust(graph, high, 0.95)
    await _set_trust(graph, low, 0.2)

    ranker = TrustAwareFeedRanker(trust_graph=graph)
    result = await ranker.rank_with_context(
        [
            _raw("c1", high.fqn, relevance=0.8),
            _raw("c2", low.fqn, relevance=0.8),
        ],
        strategy=ScoringStrategy.PRODUCT,
    )

    assert isinstance(result, FeedResult)
    assert len(result.ranked) == 2
    # High-trust candidate should rank first (higher product score).
    assert result.ranked[0].candidate_id == "c1"
    assert result.ranked[1].candidate_id == "c2"


async def test_rank_uses_default_trust_for_unknown_principal() -> None:
    graph = InMemoryTrustGraph()
    ranker = TrustAwareFeedRanker(trust_graph=graph, default_trust_score=0.5)

    result = await ranker.rank_with_context(
        [_raw("c1", "agent:unknown", relevance=0.9)],
    )
    assert len(result.ranked) == 1
    # Attention weight should be 1.0 (single candidate after normalisation).
    assert abs(result.ranked[0].attention - 1.0) < 1e-9


async def test_rank_empty_raises() -> None:
    graph = InMemoryTrustGraph()
    ranker = TrustAwareFeedRanker(trust_graph=graph)
    with pytest.raises(ValueError, match="non-empty"):
        await ranker.rank_with_context([])


# ---------------------------------------------------------------------------
# Economic signal integration
# ---------------------------------------------------------------------------


async def test_budget_exhausted_signal_penalises_trust() -> None:
    graph = InMemoryTrustGraph()
    ledger = InMemoryBudgetLedger()

    rich = _principal("rich-agent")
    broke = _principal("broke-agent")
    await _set_trust(graph, rich, 0.9)
    await _set_trust(graph, broke, 0.9)

    # Give rich agent budget; broke agent has no balance → force exhaustion signal.
    await ledger.deposit(rich, 100.0)
    # Trigger a BUDGET_EXHAUSTED signal by attempting to reserve more than available.
    try:
        await ledger.reserve(broke, 1.0)
    except Exception:
        pass  # Expected — BudgetExhausted signal is emitted

    ranker = TrustAwareFeedRanker(
        trust_graph=graph,
        signal_source=ledger,
        budget_penalty_factor=0.5,
    )
    result = await ranker.rank_with_context(
        [
            _raw("c-rich", rich.fqn, relevance=0.8),
            _raw("c-broke", broke.fqn, relevance=0.8),
        ],
        strategy=ScoringStrategy.PRODUCT,
    )
    # Rich agent has full trust; broke agent has penalised trust — rich ranks first.
    assert result.ranked[0].candidate_id == "c-rich"


async def test_no_signal_source_no_penalty() -> None:
    """Without a signal source the ranker never penalises anyone."""
    graph = InMemoryTrustGraph()
    principal = _principal("p1")
    await _set_trust(graph, principal, 0.8)

    ranker = TrustAwareFeedRanker(trust_graph=graph, signal_source=None)
    result = await ranker.rank_with_context([_raw("c1", principal.fqn)])
    assert len(result.ranked) == 1


# ---------------------------------------------------------------------------
# Sybil suppression passthrough
# ---------------------------------------------------------------------------


async def test_sybil_suppression_forwarded() -> None:
    graph = InMemoryTrustGraph()
    spammer = _principal("spammer")
    legit = _principal("legit")
    await _set_trust(graph, spammer, 0.9)
    await _set_trust(graph, legit, 0.9)

    ranker = TrustAwareFeedRanker(trust_graph=graph)
    result = await ranker.rank_with_context(
        [
            _raw("s1", spammer.fqn, 0.9),
            _raw("s2", spammer.fqn, 0.85),
            _raw("s3", spammer.fqn, 0.8),
            _raw("l1", legit.fqn, 0.7),
        ],
        sybil_max_share=0.4,  # spammer can hold max 40% attention
    )
    # At least one spammer candidate should be penalised.
    assert result.sybil_suppressed_count > 0


# ---------------------------------------------------------------------------
# top_k and attention_threshold passthrough
# ---------------------------------------------------------------------------


async def test_top_k_respected() -> None:
    graph = InMemoryTrustGraph()
    for i in range(5):
        await _set_trust(graph, _principal(f"p{i}"), 0.7)

    ranker = TrustAwareFeedRanker(trust_graph=graph)
    result = await ranker.rank_with_context(
        [_raw(f"c{i}", f"agent:t1:w1:p{i}", 0.8) for i in range(5)],
        top_k=3,
    )
    assert len(result.ranked) <= 3
