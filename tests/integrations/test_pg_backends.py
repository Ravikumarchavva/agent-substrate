"""Integration tests for the Postgres-backed stores (task store, vector store).

The runtime store has its own suite: ``test_postgres_runtime_store.py``.

Requires running Postgres.
Skip automatically when DATABASE_URL is not reachable.

Run with infra up:
    make infra-up
    uv run pytest tests/integrations/test_pg_backends.py -v
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from substrate.types import new_id

pytestmark = [pytest.mark.requires_postgres]

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

_PG_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/agentdb"
).replace("+asyncpg", "")


async def _pg_pool():
    try:
        import asyncpg

        pool = await asyncpg.create_pool(_PG_URL, min_size=1, max_size=3)
        return pool
    except Exception:
        return None


@pytest.fixture()
async def pg_pool():
    pool = await _pg_pool()
    if pool is None:
        pytest.skip("Postgres not reachable")
    yield pool
    await pool.close()


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------


async def _pg_session_factory() -> "async_sessionmaker | None":
    """Return a SQLAlchemy async_sessionmaker pointed at the test PG instance."""
    try:
        from sqlalchemy.ext.asyncio import (
            create_async_engine,
            async_sessionmaker,
            AsyncSession,
        )

        url = _PG_URL.replace("postgresql://", "postgresql+asyncpg://")
        engine = create_async_engine(url, pool_pre_ping=True)
        factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        # quick connectivity check
        async with factory() as s:
            from sqlalchemy import text

            await s.execute(text("SELECT 1"))
        return factory
    except Exception:
        return None



# ---------------------------------------------------------------------------
# PgVectorStore — configurable table_name
# ---------------------------------------------------------------------------


async def _pg_async_engine():
    try:
        from sqlalchemy.ext.asyncio import create_async_engine

        url = _PG_URL.replace("postgresql://", "postgresql+asyncpg://")
        engine = create_async_engine(url, pool_pre_ping=True)
        async with engine.connect():
            pass
        return engine
    except Exception:
        return None


def test_pg_vector_store_rejects_invalid_table_name() -> None:
    """table_name is interpolated directly into SQL (no ORM/param binding
    for identifiers) — must be validated at construction, not left to fail
    confusingly (or unsafely) at the first query."""
    from substrate.integrations.vector.pgvector_store import PgVectorStore

    with pytest.raises(ValueError):
        PgVectorStore(
            session_factory=None, engine=None, table_name="not valid; DROP TABLE x"
        )


async def test_pg_vector_store_custom_table_name_is_isolated_from_default() -> None:
    """A second PgVectorStore with a different table_name must not share
    rows (or even its table) with the default-table instance — this is the
    whole point of the parameter: one Postgres vector(N) column has a fixed
    width, so a second embedding dimensionality needs its own table."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

    from substrate.integrations.vector.pgvector_store import PgVectorStore
    from substrate.stores import Document

    engine = await _pg_async_engine()
    if engine is None:
        pytest.skip("Postgres not reachable")
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    # The default table (dimensions=384) may already exist from other tests
    # sharing this Postgres instance — ensure_table() is CREATE TABLE IF NOT
    # EXISTS, so it won't retroactively change an existing table's vector
    # width. Match that width here rather than assuming a fresh table.
    default_store = PgVectorStore(
        session_factory=session_factory, engine=engine, dimensions=384
    )
    custom_store = PgVectorStore(
        session_factory=session_factory,
        engine=engine,
        dimensions=3,
        table_name="vector_records_test_images",
    )
    await default_store.ensure_table()
    await custom_store.ensure_table()

    default_vec = [0.1] * 384
    collection = f"isolation-test-{id(object())}"
    await default_store.add(
        [Document.from_text("in the default table", embedding=default_vec)],
        collection=collection,
    )
    await custom_store.add(
        [Document.from_text("in the custom table", embedding=[0.4, 0.5, 0.6])],
        collection=collection,
    )

    default_results = await default_store.search(
        default_vec, collection=collection, limit=10
    )
    custom_results = await custom_store.search(
        [0.4, 0.5, 0.6], collection=collection, limit=10
    )

    assert len(default_results) == 1
    assert default_results[0].to_text() == "in the default table"
    assert len(custom_results) == 1
    assert custom_results[0].to_text() == "in the custom table"

    await default_store.delete_collection(collection)
    await custom_store.delete_collection(collection)


async def test_pg_vector_store_rename_collection_rekeys_rows() -> None:
    """rename_collection moves every row from one collection to another —
    the mechanism LocalRagBackend.promote() uses to move a staged document
    into a thread's real collection without re-embedding."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

    from substrate.integrations.vector.pgvector_store import PgVectorStore
    from substrate.stores import Document

    engine = await _pg_async_engine()
    if engine is None:
        pytest.skip("Postgres not reachable")
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    # Match the default table's existing dimensionality (see the isolation
    # test above) rather than assuming a fresh table.
    store = PgVectorStore(
        session_factory=session_factory, engine=engine, dimensions=384
    )
    await store.ensure_table()

    vec = [0.1] * 384
    old_collection = f"staging-rename-test-{id(object())}"
    new_collection = f"promoted-rename-test-{id(object())}"
    await store.add(
        [
            Document.from_text("page one", embedding=vec),
            Document.from_text("page two", embedding=vec),
        ],
        collection=old_collection,
    )

    moved = await store.rename_collection(old_collection, new_collection)

    assert moved == 2
    assert await store.search(vec, collection=old_collection, limit=10) == []
    new_results = await store.search(vec, collection=new_collection, limit=10)
    assert {r.to_text() for r in new_results} == {"page one", "page two"}

    await store.delete_collection(new_collection)


async def test_pg_vector_store_rename_collection_noop_when_nothing_matches() -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

    from substrate.integrations.vector.pgvector_store import PgVectorStore

    engine = await _pg_async_engine()
    if engine is None:
        pytest.skip("Postgres not reachable")
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    store = PgVectorStore(
        session_factory=session_factory, engine=engine, dimensions=384
    )
    await store.ensure_table()

    moved = await store.rename_collection(
        f"nonexistent-{id(object())}", f"also-nonexistent-{id(object())}"
    )
    assert moved == 0


# ---------------------------------------------------------------------------
# PgVectorStore — hybrid_search / lexical_search
# ---------------------------------------------------------------------------


async def _hybrid_test_store():
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

    from substrate.integrations.vector.pgvector_store import PgVectorStore

    engine = await _pg_async_engine()
    if engine is None:
        return None
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    # Match the default table's existing dimensionality (see the isolation
    # test above) rather than assuming a fresh table.
    store = PgVectorStore(
        session_factory=session_factory, engine=engine, dimensions=384
    )
    await store.ensure_table()
    return store


def _vec384(*nonzero: tuple[int, float]) -> list[float]:
    """A 384-dim vector (the shared default table's fixed width) with the
    given (index, value) pairs set, zero elsewhere."""
    v = [0.0] * 384
    for i, val in nonzero:
        v[i] = val
    return v


async def test_pg_vector_store_lexical_search_ranks_by_ts_rank() -> None:
    from substrate.stores import Document

    store = await _hybrid_test_store()
    if store is None:
        pytest.skip("Postgres not reachable")
    collection = f"lexical-test-{id(object())}"
    await store.add(
        [
            Document.from_text(
                "quarterly revenue grew 20 percent", embedding=_vec384((0, 1.0))
            ),
            Document.from_text(
                "the weather was sunny in San Francisco", embedding=_vec384((0, 1.0))
            ),
        ],
        collection=collection,
    )

    results = await store.lexical_search("revenue", collection=collection, limit=10)

    assert len(results) == 1
    assert "revenue" in results[0].to_text()

    await store.delete_collection(collection)


async def test_pg_vector_store_hybrid_search_fuses_dense_and_lexical_rank() -> None:
    """Reproduces the exact scenario hybrid search exists for: a document
    that's an exact lexical match AND a decent semantic match should outrank
    a document that's only a semantic-adjacent match, which should in turn
    outrank a document that's neither."""
    from substrate.stores import Document

    store = await _hybrid_test_store()
    if store is None:
        pytest.skip("Postgres not reachable")
    collection = f"hybrid-test-{id(object())}"
    query_vec = _vec384((0, 1.0))
    both = Document.from_text(
        "quarterly revenue grew 20 percent", embedding=_vec384((0, 0.95), (1, 0.05))
    )
    semantic_only = Document.from_text(
        "sales figures for the last quarter", embedding=_vec384((0, 0.9), (1, 0.1))
    )
    irrelevant = Document.from_text(
        "the weather was sunny in San Francisco", embedding=_vec384((2, 1.0))
    )
    await store.add([both, semantic_only, irrelevant], collection=collection)

    # dense_k=2 caps the dense candidate list to the two closest embeddings
    # (both, semantic_only) — irrelevant's orthogonal embedding falls out of
    # it, and it has no lexical match either, so it must be fully excluded.
    results = await store.hybrid_search(
        query_vec, "revenue", collection=collection, dense_k=2, lexical_k=10, fused_k=10
    )
    ids = [r.id for r in results]

    assert ids.index(both.id) < ids.index(semantic_only.id)
    assert irrelevant.id not in ids

    await store.delete_collection(collection)


async def test_pg_vector_store_hybrid_search_applies_filter() -> None:
    from substrate.stores import Document

    store = await _hybrid_test_store()
    if store is None:
        pytest.skip("Postgres not reachable")
    collection = f"hybrid-filter-test-{id(object())}"
    keep = Document.from_text(
        "revenue report", embedding=_vec384((0, 1.0)), metadata={"file_id": "keep"}
    )
    drop = Document.from_text(
        "revenue report", embedding=_vec384((0, 1.0)), metadata={"file_id": "drop"}
    )
    await store.add([keep, drop], collection=collection)

    results = await store.hybrid_search(
        _vec384((0, 1.0)), "revenue", collection=collection, filter={"file_id": "keep"}
    )

    assert [r.id for r in results] == [keep.id]

    await store.delete_collection(collection)


# ---------------------------------------------------------------------------
# PgVectorStore — multi-row INSERT (add/upsert chunking, dedup)
# ---------------------------------------------------------------------------


async def test_pg_vector_store_add_spans_multiple_insert_batches() -> None:
    """add() chunks its INSERT statements at insert_batch_size -- this
    exercises more rows than fit in one statement to prove the multi-row
    VALUES rewrite doesn't drop or corrupt rows across a chunk boundary."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

    from substrate.integrations.vector.pgvector_store import PgVectorStore
    from substrate.stores import Document

    engine = await _pg_async_engine()
    if engine is None:
        pytest.skip("Postgres not reachable")
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    store = PgVectorStore(
        session_factory=session_factory,
        engine=engine,
        dimensions=384,
        insert_batch_size=10,  # small on purpose -- forces multiple chunks
    )
    await store.ensure_table()

    collection = f"chunked-insert-test-{id(object())}"
    docs = [
        Document.from_text(f"doc {i}", embedding=_vec384((i % 384, 1.0)))
        for i in range(25)  # 3 chunks at batch size 10: 10, 10, 5
    ]
    ids = await store.add(docs, collection=collection)

    assert len(ids) == 25
    fetched = await store.get(ids, collection=collection)
    assert {d.to_text() for d in fetched} == {f"doc {i}" for i in range(25)}

    await store.delete_collection(collection)


async def test_pg_vector_store_upsert_dedupes_repeated_id_within_a_chunk() -> None:
    """ON CONFLICT DO UPDATE aborts a whole statement if one VALUES list
    repeats an id ("cannot affect row a second time") -- upsert() must
    dedupe (last write wins) before building the VALUES list, not just
    trust callers never pass duplicate ids in one call."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

    from substrate.integrations.vector.pgvector_store import PgVectorStore
    from substrate.stores import Document

    engine = await _pg_async_engine()
    if engine is None:
        pytest.skip("Postgres not reachable")
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    store = PgVectorStore(
        session_factory=session_factory, engine=engine, dimensions=384
    )
    await store.ensure_table()

    collection = f"upsert-dedup-test-{id(object())}"
    shared_id = new_id()
    docs = [
        Document.from_text("first", id=shared_id, embedding=_vec384((0, 1.0))),
        Document.from_text(
            "second (should win)", id=shared_id, embedding=_vec384((0, 1.0))
        ),
    ]

    # Must not raise, despite both documents sharing shared_id.
    ids = await store.upsert(docs, collection=collection)

    assert ids == [shared_id, shared_id]
    fetched = await store.get([shared_id], collection=collection)
    assert len(fetched) == 1
    assert fetched[0].to_text() == "second (should win)"

    await store.delete_collection(collection)
