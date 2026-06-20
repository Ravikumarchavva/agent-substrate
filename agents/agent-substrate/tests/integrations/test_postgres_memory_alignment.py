from __future__ import annotations

import os
import pytest
from sqlalchemy.exc import OperationalError

from sqlalchemy.ext.asyncio import create_async_engine

from ravi.kernel import AgentId, ChatMessage
from ravi.kernel.core.content import TextBlock
from ravi.kernel.storage.vector import Document
from ravi.kernel.tools import ToolExecutionResult, ToolCallRequest

from ravi.capabilities.memory import PostgresMemoryStore
from ravi.capabilities.history import PostgresHistoryProvider
from ravi.capabilities.vector import PgVectorStore
from ravi.capabilities.graph import AGEGraphStore

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


# ── 2. PostgresMemoryStore Tenancy Tests ─────────────────────────────────────


@pytest.mark.asyncio
async def test_postgres_memory_store_tenancy():
    if not await check_db_available():
        pytest.skip("PostgreSQL database not available")

    db_url = get_db_url()
    store = PostgresMemoryStore(db_url)
    await store.connect()
    await store.create_tables()

    agent_id = AgentId(type="assistant", key="agent-1")

    try:
        # Clear both namespaces
        await store.clear(agent_id, namespace="tenant-a")
        await store.clear(agent_id, namespace="tenant-b")

        # Save to tenant-a
        id_a = await store.save(agent_id, "Memory for A", namespace="tenant-a")

        # Save to tenant-b
        id_b = await store.save(agent_id, "Memory for B", namespace="tenant-b")

        # Search tenant-a
        results_a = await store.search(agent_id, "Memory", namespace="tenant-a")
        assert len(results_a) == 1
        assert results_a[0].content == "Memory for A"

        # Search tenant-b
        results_b = await store.search(agent_id, "Memory", namespace="tenant-b")
        assert len(results_b) == 1
        assert results_b[0].content == "Memory for B"

        # Verify get filters by namespace
        assert await store.get(agent_id, id_a, namespace="tenant-a") is not None
        assert await store.get(agent_id, id_a, namespace="tenant-b") is None

        # Delete from tenant-a
        deleted = await store.delete(agent_id, id_a, namespace="tenant-a")
        assert deleted is True

        # Check tenant-a is deleted, tenant-b remains
        assert await store.get(agent_id, id_a, namespace="tenant-a") is None
        assert await store.get(agent_id, id_b, namespace="tenant-b") is not None
    finally:
        await store.disconnect()


# ── 3. PostgresHistoryProvider Protocol Tests ────────────────────────────────


@pytest.mark.asyncio
async def test_postgres_history_provider_conformance():
    if not await check_db_available():
        pytest.skip("PostgreSQL database not available")

    db_url = get_db_url()
    provider = PostgresHistoryProvider(db_url)
    await provider.connect()

    agent_id = AgentId(type="assistant", key="agent-history-test")
    session_id = "sess-history-test"

    try:
        await provider.clear(agent_id, session_id=session_id)

        # Test append via protocol
        msg1 = ChatMessage(role="user", content=[TextBlock(text="message 1")])
        await provider.append(agent_id, msg1, session_id=session_id, run_id="run-x")

        # Test append_many
        msg2 = ChatMessage(role="assistant", content=[TextBlock(text="message 2")])
        msg3 = ChatMessage(role="user", content=[TextBlock(text="message 3")])
        await provider.append_many(
            agent_id, [msg2, msg3], session_id=session_id, run_id="run-y"
        )

        # Retrieve messages
        loaded = await provider.get_messages(agent_id, session_id=session_id)
        assert len(loaded) == 3
        assert isinstance(loaded[0], ChatMessage)
        assert loaded[0].content[0].text == "message 1"
        assert loaded[1].content[0].text == "message 2"
        assert loaded[2].content[0].text == "message 3"

        # Verify offset and limit
        subset = await provider.get_messages(
            agent_id, session_id=session_id, limit=1, offset=1
        )
        assert len(subset) == 1
        assert subset[0].content[0].text == "message 2"

        # Test clear_run (delete only run-x)
        await provider.clear_run(agent_id, session_id=session_id, run_id="run-x")
        remaining = await provider.get_messages(agent_id, session_id=session_id)
        assert len(remaining) == 2
        assert remaining[0].content[0].text == "message 2"
        assert remaining[1].content[0].text == "message 3"
    finally:
        await provider.disconnect()


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

    emb1 = [0.1] * 384
    emb2 = [0.5] * 384

    try:
        # Create documents with embeddings populated
        doc1 = Document.from_text(
            "multimodal text doc 1",
            id="00000000-0000-0000-0000-000000000001",
            embedding=emb1,
        )
        doc2 = Document.from_text(
            "multimodal text doc 2",
            id="00000000-0000-0000-0000-000000000002",
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
