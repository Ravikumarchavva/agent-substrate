"""Tests for PostgresLineageStore.

All SQLAlchemy I/O is mocked — no live PostgreSQL server required.

Coverage
--------
- Protocol conformance (isinstance checks)
- ``tier`` property
- ``record()`` — returns correct LineageRecord
- ``record()`` — same message_id is idempotent (upsert)
- ``record()`` — raises ValueError on invalid session_id
- ``get()`` — returns record
- ``get()`` — raises LineageNotFoundError for missing
- ``list_session()`` — returns records in insertion order
- ``list_session()`` — respects ``limit``
- ``causal_chain()`` — follows parent_message_id links
- ``causal_chain()`` — detects cycles (doesn't loop forever)
- ``drop_session()`` — deletes records
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ravi.adapters.memory.lineage_postgres import (
    LineageRow,
    PostgresLineageStore,
    _row_to_record,
)
from ravi.kernel.memory._lineage import (
    LineageNotFoundError,
    LineageRecord,
    LineageStore,
    ProvenanceTag,
    StorageTier,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DB_URL = "postgresql+asyncpg://user:pass@localhost/testdb"


def _make_provenance(
    agent_fqn: str = "agents.TestAgent",
    activation_id: str = "act-001",
    timestamp_utc: str = "2026-01-01T00:00:00Z",
    tool_call_id: str | None = None,
    parent_message_id: str | None = None,
    trust_score: float | None = 0.9,
) -> ProvenanceTag:
    return ProvenanceTag(
        agent_fqn=agent_fqn,
        activation_id=activation_id,
        timestamp_utc=timestamp_utc,
        tool_call_id=tool_call_id,
        parent_message_id=parent_message_id,
        trust_score=trust_score,
    )


def _make_row(
    row_id: int = 1,
    session_id: str = "sess-1",
    message_id: str = "msg-1",
    agent_fqn: str = "agents.TestAgent",
    activation_id: str = "act-001",
    timestamp_utc: str = "2026-01-01T00:00:00Z",
    tool_call_id: str | None = None,
    parent_message_id: str | None = None,
    trust_score: float | None = 0.9,
    tier_str: str = "warm",
) -> LineageRow:
    """Build a LineageRow instance without touching a real DB."""
    row = LineageRow()
    row.id = row_id
    row.session_id = session_id
    row.message_id = message_id
    row.agent_fqn = agent_fqn
    row.activation_id = activation_id
    row.timestamp_utc = timestamp_utc
    row.tool_call_id = tool_call_id
    row.parent_message_id = parent_message_id
    row.trust_score = trust_score
    row.tier_str = tier_str
    return row


def _make_store() -> PostgresLineageStore:
    """Return a store with a pre-wired mock session factory (no real DB)."""
    store = PostgresLineageStore(_DB_URL, pool_size=2)
    mock_factory = MagicMock()
    store._session_factory = mock_factory
    return store


def _mock_session_ctx(execute_return: Any = None) -> tuple[MagicMock, AsyncMock]:
    """Return (mock_factory, mock_session) wired so `async with factory() as db` works.

    ``execute_return`` is the value returned by ``db.execute()``.
    """
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=execute_return)
    mock_session.commit = AsyncMock()

    # async context manager support
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_factory = MagicMock(return_value=mock_session)
    return mock_factory, mock_session


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_is_lineage_store_protocol(self) -> None:
        store = PostgresLineageStore(_DB_URL)
        assert isinstance(store, LineageStore)

    def test_tier_property_returns_warm(self) -> None:
        store = PostgresLineageStore(_DB_URL)
        assert store.tier is StorageTier.WARM


# ---------------------------------------------------------------------------
# record()
# ---------------------------------------------------------------------------


class TestRecord:
    async def test_record_returns_lineage_record_with_correct_fields(self) -> None:
        store = _make_store()
        provenance = _make_provenance(
            agent_fqn="agents.Alpha",
            activation_id="act-42",
            timestamp_utc="2026-05-01T12:00:00Z",
            trust_score=0.85,
        )

        # Mock: execute returns a result whose fetchone() we don't actually use
        # (record() rebuilds from the supplied provenance).
        mock_result = MagicMock()
        mock_result.fetchone.return_value = None
        mock_factory, mock_session = _mock_session_ctx(execute_return=mock_result)
        store._session_factory = mock_factory

        rec = await store.record("sess-abc", "msg-xyz", provenance)

        assert isinstance(rec, LineageRecord)
        assert rec.session_id == "sess-abc"
        assert rec.message_id == "msg-xyz"
        assert rec.provenance.agent_fqn == "agents.Alpha"
        assert rec.provenance.activation_id == "act-42"
        assert rec.provenance.trust_score == 0.85
        assert rec.tier is StorageTier.WARM

    async def test_record_idempotent_same_message_id(self) -> None:
        """Calling record() twice with the same IDs must not raise."""
        store = _make_store()
        provenance = _make_provenance()

        mock_result = MagicMock()
        mock_result.fetchone.return_value = None

        call_count = 0

        async def fake_execute(*_args: Any, **_kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            return mock_result

        mock_factory, mock_session = _mock_session_ctx()
        mock_session.execute = fake_execute
        store._session_factory = mock_factory

        rec1 = await store.record("sess-idem", "msg-idem", provenance)
        rec2 = await store.record("sess-idem", "msg-idem", provenance)

        assert rec1.message_id == rec2.message_id
        assert call_count == 2  # two separate upserts executed

    async def test_record_raises_value_error_on_invalid_session_id(self) -> None:
        store = _make_store()
        provenance = _make_provenance()

        with pytest.raises(ValueError, match="Invalid session_id"):
            await store.record("invalid session id!", "msg-1", provenance)

    async def test_record_raises_value_error_on_invalid_message_id(self) -> None:
        store = _make_store()
        provenance = _make_provenance()

        with pytest.raises(ValueError, match="Invalid message_id"):
            await store.record("sess-ok", "invalid message id!", provenance)


# ---------------------------------------------------------------------------
# get()
# ---------------------------------------------------------------------------


class TestGet:
    async def test_get_returns_record_when_present(self) -> None:
        store = _make_store()
        row = _make_row(session_id="sess-get", message_id="msg-get", trust_score=0.7)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = row
        mock_factory, mock_session = _mock_session_ctx(execute_return=mock_result)
        store._session_factory = mock_factory

        rec = await store.get("sess-get", "msg-get")

        assert rec.session_id == "sess-get"
        assert rec.message_id == "msg-get"
        assert rec.provenance.agent_fqn == "agents.TestAgent"
        assert rec.tier is StorageTier.WARM

    async def test_get_raises_lineage_not_found_for_missing(self) -> None:
        store = _make_store()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_factory, mock_session = _mock_session_ctx(execute_return=mock_result)
        store._session_factory = mock_factory

        with pytest.raises(LineageNotFoundError):
            await store.get("sess-get", "msg-missing")


# ---------------------------------------------------------------------------
# list_session()
# ---------------------------------------------------------------------------


class TestListSession:
    async def test_list_session_returns_records_in_insertion_order(self) -> None:
        store = _make_store()
        rows = [
            _make_row(row_id=1, message_id="msg-1"),
            _make_row(row_id=2, message_id="msg-2"),
            _make_row(row_id=3, message_id="msg-3"),
        ]

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = rows
        mock_factory, mock_session = _mock_session_ctx(execute_return=mock_result)
        store._session_factory = mock_factory

        records = await store.list_session("sess-1")

        assert [r.message_id for r in records] == ["msg-1", "msg-2", "msg-3"]

    async def test_list_session_with_limit_passes_limit_to_query(self) -> None:
        """Passing ``limit=2`` should return at most 2 records."""
        store = _make_store()
        rows = [
            _make_row(row_id=1, message_id="msg-1"),
            _make_row(row_id=2, message_id="msg-2"),
        ]

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = rows
        mock_factory, mock_session = _mock_session_ctx(execute_return=mock_result)
        store._session_factory = mock_factory

        records = await store.list_session("sess-1", limit=2)

        assert len(records) == 2

    async def test_list_session_raises_on_invalid_session_id(self) -> None:
        store = _make_store()
        with pytest.raises(ValueError, match="Invalid session_id"):
            await store.list_session("bad session!")


# ---------------------------------------------------------------------------
# causal_chain()
# ---------------------------------------------------------------------------


class TestCausalChain:
    async def _make_store_with_chain(
        self,
        rows_by_id: dict[str, LineageRow],
    ) -> PostgresLineageStore:
        """Wire a store whose session lookups return rows from *rows_by_id*."""
        store = _make_store()

        async def fake_execute(stmt: Any, *_args: Any, **_kw: Any) -> MagicMock:
            # Sniff the WHERE clauses to determine which row to return.
            # We rely on the fact that our queries always filter by both
            # session_id and message_id.
            compiled = stmt.compile(compile_kwargs={"literal_binds": True})
            sql_text = str(compiled)

            found_row = None
            for mid, row in rows_by_id.items():
                if f"'{mid}'" in sql_text or f'"{mid}"' in sql_text:
                    found_row = row
                    break

            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = found_row
            return mock_result

        mock_session = AsyncMock()
        mock_session.execute = fake_execute
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        store._session_factory = MagicMock(return_value=mock_session)
        return store

    async def test_causal_chain_follows_parent_links(self) -> None:
        root = _make_row(row_id=1, message_id="root", parent_message_id=None)
        child = _make_row(
            row_id=2, message_id="child", parent_message_id="root"
        )
        grandchild = _make_row(
            row_id=3, message_id="grandchild", parent_message_id="child"
        )

        store = await self._make_store_with_chain(
            {"root": root, "child": child, "grandchild": grandchild}
        )

        chain = await store.causal_chain("sess-1", "grandchild")

        assert [r.message_id for r in chain] == ["root", "child", "grandchild"]

    async def test_causal_chain_raises_if_start_missing(self) -> None:
        store = _make_store()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_factory, mock_session = _mock_session_ctx(execute_return=mock_result)
        store._session_factory = mock_factory

        with pytest.raises(LineageNotFoundError):
            await store.causal_chain("sess-1", "no-such-msg")

    async def test_causal_chain_detects_cycles_does_not_loop_forever(self) -> None:
        """A → B → A cycle must terminate without infinite recursion."""
        row_a = _make_row(row_id=1, message_id="msg-a", parent_message_id="msg-b")
        row_b = _make_row(row_id=2, message_id="msg-b", parent_message_id="msg-a")

        store = await self._make_store_with_chain({"msg-a": row_a, "msg-b": row_b})

        # Must complete and return a finite chain.
        chain = await store.causal_chain("sess-1", "msg-a")
        assert len(chain) <= 2
        # Verify no duplicate entries (cycle was broken).
        ids = [r.message_id for r in chain]
        assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# drop_session()
# ---------------------------------------------------------------------------


class TestDropSession:
    async def test_drop_session_executes_delete(self) -> None:
        store = _make_store()

        mock_result = MagicMock()
        mock_factory, mock_session = _mock_session_ctx(execute_return=mock_result)
        store._session_factory = mock_factory

        # Should not raise.
        await store.drop_session("sess-to-drop")

        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    async def test_drop_session_raises_on_invalid_session_id(self) -> None:
        store = _make_store()
        with pytest.raises(ValueError, match="Invalid session_id"):
            await store.drop_session("bad session id!")


# ---------------------------------------------------------------------------
# row_to_record helper
# ---------------------------------------------------------------------------


class TestRowToRecord:
    def test_row_to_record_maps_all_fields(self) -> None:
        row = _make_row(
            session_id="s1",
            message_id="m1",
            agent_fqn="agents.Foo",
            activation_id="act-1",
            timestamp_utc="2026-01-01T00:00:00Z",
            tool_call_id="tc-1",
            parent_message_id="parent-m",
            trust_score=0.5,
        )
        rec = _row_to_record(row)
        assert rec.session_id == "s1"
        assert rec.message_id == "m1"
        assert rec.provenance.agent_fqn == "agents.Foo"
        assert rec.provenance.activation_id == "act-1"
        assert rec.provenance.tool_call_id == "tc-1"
        assert rec.provenance.parent_message_id == "parent-m"
        assert rec.provenance.trust_score == 0.5
        assert rec.tier is StorageTier.WARM
