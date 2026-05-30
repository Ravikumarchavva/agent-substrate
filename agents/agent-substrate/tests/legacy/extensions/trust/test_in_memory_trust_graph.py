"""Tests for the :class:`InMemoryTrustGraph` reference implementation.

These pin the behavioral contract that any production-backed trust graph
(Redis, Postgres, Neo4j) must match:

- Ingest records signals
- ``score_for`` composes recent signals into a normalised :class:`TrustScore`
- Expired signals are skipped on read
- ``decay_expired`` GCs expired entries and reports the count
- Unknown principals return ``None``
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ravi.guardrails.trust import InMemoryTrustGraph
from ravi.kernel.contracts._coordination import TrustSignal
from ravi.kernel.contracts._trust import TrustGraph
from ravi.kernel.runtime._identity import PrincipalId, PrincipalKind

UTC = timezone.utc


def _principal(name: str = "alice") -> PrincipalId:
    return PrincipalId(
        kind=PrincipalKind.AGENT,
        tenant_id="t1",
        workspace_id="w1",
        name=name,
    )


def _signal(
    value: float, *, expires_in: float | None = None, source: str = "test"
) -> TrustSignal:
    expires_at = None
    if expires_in is not None:
        expires_at = (datetime.now(UTC) + timedelta(seconds=expires_in)).isoformat()
    return TrustSignal(
        signal_type="moderation_pass",
        value=value,
        source_id=source,
        issued_at=datetime.now(UTC).isoformat(),
        expires_at=expires_at,
    )


class TestProtocolConformance:
    async def test_isinstance_trust_graph(self) -> None:
        assert isinstance(InMemoryTrustGraph(), TrustGraph)


class TestLookupBeforeIngest:
    async def test_unknown_principal_returns_none(self) -> None:
        graph = InMemoryTrustGraph()
        assert await graph.score_for(_principal()) is None

    async def test_signals_for_unknown_returns_empty(self) -> None:
        graph = InMemoryTrustGraph()
        assert await graph.signals_for(_principal()) == ()


class TestIngestAndScore:
    async def test_single_signal_returns_its_value(self) -> None:
        graph = InMemoryTrustGraph()
        pid = _principal()
        await graph.ingest(pid, _signal(0.8))
        score = await graph.score_for(pid)
        assert score is not None
        assert score.value == pytest.approx(0.8, abs=0.01)
        assert score.source == "in_memory_trust_graph"

    async def test_multiple_signals_weighted_toward_recent(self) -> None:
        """Most recent signal carries the heaviest weight (linear by age)."""
        graph = InMemoryTrustGraph()
        pid = _principal()
        await graph.ingest(pid, _signal(0.0))  # oldest, weight = 1/3
        await graph.ingest(pid, _signal(0.5))  # middle,  weight = 2/3
        await graph.ingest(pid, _signal(1.0))  # newest,  weight = 3/3
        score = await graph.score_for(pid)
        assert score is not None
        # Weighted avg: (1/3*0 + 2/3*0.5 + 3/3*1.0) / (1/3 + 2/3 + 1) = 4/3 / 2 = 0.667
        assert score.value == pytest.approx(0.667, abs=0.01)

    async def test_score_clamped_to_unit_interval(self) -> None:
        graph = InMemoryTrustGraph()
        pid = _principal()
        # Pathological positive signal (shouldn't happen in practice but verify)
        await graph.ingest(pid, _signal(1.0))
        score = await graph.score_for(pid)
        assert score is not None
        assert 0.0 <= score.value <= 1.0

    async def test_signals_per_principal_isolated(self) -> None:
        graph = InMemoryTrustGraph()
        a, b = _principal("alice"), _principal("bob")
        await graph.ingest(a, _signal(0.9))
        await graph.ingest(b, _signal(0.1))
        score_a = await graph.score_for(a)
        score_b = await graph.score_for(b)
        assert score_a is not None and score_a.value == pytest.approx(0.9)
        assert score_b is not None and score_b.value == pytest.approx(0.1)


class TestExpirySemantics:
    async def test_expired_signal_skipped_on_read(self) -> None:
        graph = InMemoryTrustGraph()
        pid = _principal()
        # expires_in negative → already expired
        await graph.ingest(pid, _signal(0.9, expires_in=-1.0))
        assert await graph.score_for(pid) is None

    async def test_mix_of_live_and_expired_signals(self) -> None:
        graph = InMemoryTrustGraph()
        pid = _principal()
        await graph.ingest(pid, _signal(0.1, expires_in=-1.0))  # expired
        await graph.ingest(pid, _signal(0.9))  # live
        score = await graph.score_for(pid)
        assert score is not None
        # Expired signal is skipped → only 0.9 counts
        assert score.value == pytest.approx(0.9)

    async def test_decay_expired_removes_and_counts(self) -> None:
        graph = InMemoryTrustGraph()
        pid = _principal()
        await graph.ingest(pid, _signal(0.1, expires_in=-1.0))
        await graph.ingest(pid, _signal(0.2, expires_in=-1.0))
        await graph.ingest(pid, _signal(0.9))
        removed = await graph.decay_expired()
        assert removed == 2
        signals = await graph.signals_for(pid)
        assert len(signals) == 1
        assert signals[0].value == pytest.approx(0.9)

    async def test_decay_drops_empty_principal_buckets(self) -> None:
        graph = InMemoryTrustGraph()
        pid = _principal()
        await graph.ingest(pid, _signal(0.1, expires_in=-1.0))
        assert graph.known_principals() == 1
        removed = await graph.decay_expired()
        assert removed == 1
        assert graph.known_principals() == 0


class TestCapacity:
    async def test_per_principal_cap_evicts_oldest(self) -> None:
        graph = InMemoryTrustGraph(per_principal_capacity=3)
        pid = _principal()
        await graph.ingest(pid, _signal(0.0, source="a"))
        await graph.ingest(pid, _signal(0.5, source="b"))
        await graph.ingest(pid, _signal(1.0, source="c"))
        await graph.ingest(pid, _signal(0.7, source="d"))  # evicts "a"
        signals = await graph.signals_for(pid)
        assert [s.source_id for s in signals] == ["b", "c", "d"]


class TestInvalidConfig:
    def test_zero_capacity_rejected(self) -> None:
        with pytest.raises(ValueError):
            InMemoryTrustGraph(per_principal_capacity=0)

    def test_zero_decay_rejected(self) -> None:
        with pytest.raises(ValueError):
            InMemoryTrustGraph(decay_seconds=0)
