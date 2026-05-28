"""Tests for Section 9 — Memory + Graph Redesign (LineageStore).

Coverage
--------
- Kernel contracts: ProvenanceTag, LineageRecord, StorageTier shapes
- InMemoryLineageStore: record, get, list_session, causal_chain, drop_session
- Input validation (session_id, message_id)
- Causal chain traversal and cycle detection
- LineageStore Protocol conformance
"""

from __future__ import annotations

import pytest

from ravi.extensions.memory._lineage import InMemoryLineageStore
from ravi.kernel.memory import (
    LineageNotFoundError,
    LineageRecord,
    LineageStore,
    ProvenanceTag,
    StorageTier,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tag(
    agent: str = "agent/t/ws/alice",
    activation: str = "act-001",
    *,
    parent: str | None = None,
    tool_call: str | None = None,
    trust: float | None = None,
) -> ProvenanceTag:
    return ProvenanceTag(
        agent_fqn=agent,
        activation_id=activation,
        timestamp_utc="2025-01-01T00:00:00Z",
        parent_message_id=parent,
        tool_call_id=tool_call,
        trust_score=trust,
    )


# ===========================================================================
# Protocol conformance
# ===========================================================================


class TestLineageStoreProtocolConformance:
    def test_in_memory_satisfies_protocol(self) -> None:
        store = InMemoryLineageStore()
        assert isinstance(store, LineageStore)

    def test_default_tier_is_hot(self) -> None:
        store = InMemoryLineageStore()
        assert store.tier is StorageTier.HOT

    def test_custom_tier_preserved(self) -> None:
        store = InMemoryLineageStore(tier=StorageTier.WARM)
        assert store.tier is StorageTier.WARM


# ===========================================================================
# Record and retrieval
# ===========================================================================


class TestRecordAndGet:
    async def test_record_returns_lineage_record(self) -> None:
        store = InMemoryLineageStore()
        rec = await store.record("sess-1", "msg-1", _tag())
        assert isinstance(rec, LineageRecord)
        assert rec.session_id == "sess-1"
        assert rec.message_id == "msg-1"

    async def test_get_returns_recorded_provenance(self) -> None:
        store = InMemoryLineageStore()
        tag = _tag(trust=0.9)
        await store.record("sess-1", "msg-1", tag)
        rec = await store.get("sess-1", "msg-1")
        assert rec.provenance.trust_score == pytest.approx(0.9)

    async def test_record_overwrites_on_duplicate_message_id(self) -> None:
        store = InMemoryLineageStore()
        await store.record("s", "m", _tag(agent="a"))
        await store.record("s", "m", _tag(agent="b"))
        rec = await store.get("s", "m")
        assert rec.provenance.agent_fqn == "b"

    async def test_get_raises_not_found_for_unknown_session(self) -> None:
        store = InMemoryLineageStore()
        with pytest.raises(LineageNotFoundError):
            await store.get("no-session", "msg-x")

    async def test_get_raises_not_found_for_unknown_message(self) -> None:
        store = InMemoryLineageStore()
        await store.record("s", "m1", _tag())
        with pytest.raises(LineageNotFoundError):
            await store.get("s", "no-such-message")

    async def test_tier_stored_in_record(self) -> None:
        store = InMemoryLineageStore(tier=StorageTier.COLD)
        rec = await store.record("s", "m", _tag())
        assert rec.tier is StorageTier.COLD


# ===========================================================================
# list_session
# ===========================================================================


class TestListSession:
    async def test_list_returns_messages_oldest_first(self) -> None:
        store = InMemoryLineageStore()
        for i in range(5):
            await store.record("s", f"msg-{i}", _tag())
        records = await store.list_session("s")
        assert [r.message_id for r in records] == [f"msg-{i}" for i in range(5)]

    async def test_list_with_limit(self) -> None:
        store = InMemoryLineageStore()
        for i in range(10):
            await store.record("s", f"m{i}", _tag())
        records = await store.list_session("s", limit=3)
        assert len(records) == 3
        # Last 3 messages
        assert records[-1].message_id == "m9"

    async def test_list_empty_session_returns_empty(self) -> None:
        store = InMemoryLineageStore()
        records = await store.list_session("s")
        assert records == [] or list(records) == []

    async def test_list_sessions_are_isolated(self) -> None:
        store = InMemoryLineageStore()
        await store.record("s1", "m", _tag())
        await store.record("s2", "m", _tag())
        r1 = await store.list_session("s1")
        r2 = await store.list_session("s2")
        assert len(r1) == 1
        assert len(r2) == 1
        assert r1[0].session_id == "s1"


# ===========================================================================
# Causal chain
# ===========================================================================


class TestCausalChain:
    async def test_chain_single_root(self) -> None:
        store = InMemoryLineageStore()
        await store.record("s", "root", _tag(parent=None))
        chain = await store.causal_chain("s", "root")
        assert len(chain) == 1
        assert chain[0].message_id == "root"

    async def test_chain_three_deep(self) -> None:
        store = InMemoryLineageStore()
        await store.record("s", "a", _tag(parent=None))
        await store.record("s", "b", _tag(parent="a"))
        await store.record("s", "c", _tag(parent="b"))
        chain = await store.causal_chain("s", "c")
        assert [r.message_id for r in chain] == ["a", "b", "c"]

    async def test_chain_stops_at_missing_parent(self) -> None:
        """If a parent_message_id has no lineage record, traversal stops."""
        store = InMemoryLineageStore()
        # 'b' points to 'a' which has no record
        await store.record("s", "b", _tag(parent="a"))
        chain = await store.causal_chain("s", "b")
        assert [r.message_id for r in chain] == ["b"]

    async def test_chain_raises_not_found_for_unknown_leaf(self) -> None:
        store = InMemoryLineageStore()
        with pytest.raises(LineageNotFoundError):
            await store.causal_chain("s", "ghost")

    async def test_chain_cycle_detection(self) -> None:
        """Cycle: a→b→c→a — must not loop forever."""
        store = InMemoryLineageStore()
        await store.record("s", "a", _tag(parent="c"))
        await store.record("s", "b", _tag(parent="a"))
        await store.record("s", "c", _tag(parent="b"))
        # Should return without hanging (exact length ≤ 3 because cycle ends)
        chain = await store.causal_chain("s", "c")
        assert len(chain) <= 3


# ===========================================================================
# Drop session
# ===========================================================================


class TestDropSession:
    async def test_drop_removes_all_records(self) -> None:
        store = InMemoryLineageStore()
        await store.record("s", "m1", _tag())
        await store.record("s", "m2", _tag())
        await store.drop_session("s")
        records = await store.list_session("s")
        assert list(records) == []

    async def test_drop_idempotent_for_nonexistent_session(self) -> None:
        store = InMemoryLineageStore()
        # Must not raise
        await store.drop_session("ghost-session")

    async def test_drop_does_not_affect_other_sessions(self) -> None:
        store = InMemoryLineageStore()
        await store.record("keep", "m", _tag())
        await store.record("drop", "m", _tag())
        await store.drop_session("drop")
        remaining = await store.list_session("keep")
        assert len(remaining) == 1


# ===========================================================================
# Input validation
# ===========================================================================


class TestInputValidation:
    async def test_invalid_session_id_raises_value_error_on_record(self) -> None:
        store = InMemoryLineageStore()
        with pytest.raises(ValueError, match="session_id"):
            await store.record("bad session!", "m", _tag())

    async def test_invalid_session_id_raises_on_get(self) -> None:
        store = InMemoryLineageStore()
        with pytest.raises(ValueError, match="session_id"):
            await store.get("bad!", "m")

    async def test_invalid_session_id_raises_on_list(self) -> None:
        store = InMemoryLineageStore()
        with pytest.raises(ValueError, match="session_id"):
            await store.list_session("bad!")

    async def test_invalid_session_id_raises_on_drop(self) -> None:
        store = InMemoryLineageStore()
        with pytest.raises(ValueError, match="session_id"):
            await store.drop_session("bad!")

    async def test_invalid_message_id_raises_value_error(self) -> None:
        store = InMemoryLineageStore()
        with pytest.raises(ValueError, match="message_id"):
            await store.record("sess-ok", "has spaces !", _tag())

    async def test_valid_session_id_with_hyphens_and_underscores(self) -> None:
        store = InMemoryLineageStore()
        rec = await store.record("my-session_01", "msg-1", _tag())
        assert rec.session_id == "my-session_01"

    async def test_valid_message_id_with_colon_slash(self) -> None:
        store = InMemoryLineageStore()
        rec = await store.record("s", "tool:call/1", _tag())
        assert rec.message_id == "tool:call/1"
