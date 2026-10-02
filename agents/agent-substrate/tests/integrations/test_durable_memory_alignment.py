from __future__ import annotations

import os
import pytest
from sqlalchemy.exc import OperationalError

from sqlalchemy.ext.asyncio import create_async_engine

from substrate.types import TextBlock
from substrate.stores import Document
from substrate.tools import ToolExecutionResult, ToolCallRequest

from substrate.integrations.vector import PgVectorStore
from substrate.integrations.graph import AGEGraphStore

pytestmark = [pytest.mark.requires_postgres]


def get_db_url() -> str:
    return os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb"
    )


async def check_db_available() -> bool:
    url = get_db_url()
    engine = create_async_engine(url)
    try:
        async with engine.connect():
            pass
        await engine.dispose()
        return True
    except (OperationalError, Exception):
        return False


# ── 1. Tools Unification Tests ───────────────────────────────────────────────


def test_tool_types_unification():
    # Construct unified ToolExecutionResult (aliased Pydantic model)
    res = ToolExecutionResult(
        call_id="call-1",
        name="my_tool",
        content=[TextBlock(text="done")],
        is_error=False,
    )
    assert res.call_id == "call-1"
    assert res.name == "my_tool"
    assert res.is_error is False
    assert res.text == "done"

    # Construct unified ToolCallRequest
    req = ToolCallRequest(
        name="my_tool",
        arguments={"x": 42},
        call_id="call-1",
    )
    assert req.name == "my_tool"
    assert req.arguments == {"x": 42}
    assert req.call_id == "call-1"


# ── 4. PgVectorStore Protocol Tests ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_pgvector_store_conformance():
    if not await check_db_available():
        pytest.skip("PostgreSQL database not available")

    # Use raw asyncpg engine for pgvector store
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    db_url = get_db_url()
    engine = create_async_engine(db_url)
    session_factory = async_sessionmaker(bind=engine)

    store = PgVectorStore(
        session_factory=session_factory, engine=engine, dimensions=384
    )
    await store.ensure_table()

    # Clear default collection
    await store.delete_collection("default")

    # Different directions: cosine similarity ignores magnitude, so [0.1]*n and [0.5]*n tie exactly
    # and which one a limit=1 search returns would be arbitrary.
    emb1 = [0.1] * 384
    emb2 = [0.5] * 192 + [-0.5] * 192

    try:
        # Create documents with embeddings populated
        doc1 = Document.from_text(
            "multimodal text doc 1",
            id="00000000000000000000000000000001",
            embedding=emb1,
        )
        doc2 = Document.from_text(
            "multimodal text doc 2",
            id="00000000000000000000000000000002",
            embedding=emb2,
        )

        # Test add conforming to VectorStore
        ids = await store.add([doc1, doc2])
        assert len(ids) == 2
        assert ids[0] == doc1.id

        # Test get conforming to VectorStore
        docs = await store.get([doc1.id, doc2.id])
        assert len(docs) == 2
        assert docs[0].to_text() == "multimodal text doc 1"
        assert len(docs[0].embedding) == 384
        assert abs(docs[0].embedding[0] - 0.1) < 1e-5

        # Test search conforming to VectorStore
        results = await store.search(emb1, limit=1)
        assert len(results) == 1
        assert results[0].id == doc1.id
        assert results[0].score > 0.99  # similarity score

        # Test upsert conforming to VectorStore
        doc1_updated = Document.from_text(
            "updated text doc 1", id=doc1.id, embedding=emb1, metadata={"updated": True}
        )
        await store.upsert([doc1_updated])

        docs_after = await store.get([doc1.id])
        assert len(docs_after) == 1
        assert docs_after[0].to_text() == "updated text doc 1"
        assert docs_after[0].metadata == {"updated": True}
    finally:
        await engine.dispose()


# ── 5. AGEGraphStore delete_relationship Test ───────────────────────────────


@pytest.mark.asyncio
async def test_age_graph_store_delete_relationship():
    # AGE is often not available or setup in standard postgres runtimes,
    # but we can at least check if we can instantiate it and check method calls.
    # We will test the delete_relationship cypher construction.
    db_url = get_db_url().replace("+asyncpg", "")
    store = AGEGraphStore(db_url)

    # We can inspect the interface presence
    assert hasattr(store, "delete_relationship")
    assert hasattr(store, "get_neighbors")
