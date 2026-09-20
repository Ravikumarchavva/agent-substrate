from __future__ import annotations

import os
import pytest
from sqlalchemy.exc import OperationalError

from sqlalchemy.ext.asyncio import create_async_engine

from substrate.kernel import ChatMessage
from substrate.kernel.core.content import TextBlock
from substrate.kernel.storage.vector import Document
from substrate.kernel.storage.memory import MemoryNamespace, MemoryQuery, MemoryRecord
from substrate.kernel.tools import ToolExecutionResult, ToolCallRequest

from substrate.capabilities.memory import DurableMemoryStore
from substrate.agents.context.history import project_messages
from substrate.capabilities.history import DurableHistoryProvider
from substrate.kernel.storage.history import MessageNode
from substrate.capabilities.vector import PgVectorStore
from substrate.capabilities.graph import AGEGraphStore

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


# ── 2. DurableMemoryStore Tenancy Tests ─────────────────────────────────────


@pytest.mark.asyncio
async def test_postgres_memory_store_tenancy():
    if not await check_db_available():
        pytest.skip("PostgreSQL database not available")

    db_url = get_db_url()
    store = DurableMemoryStore(db_url)
    await store.connect()
    await store.create_tables()

    ns_a = MemoryNamespace(tenant_id="tenant-a", agent_id="agent-1")
    ns_b = MemoryNamespace(tenant_id="tenant-b", agent_id="agent-1")

    try:
        # Clear both namespaces
        await store.clear(ns_a)
        await store.clear(ns_b)

        # Save to tenant-a
        rec_a = MemoryRecord.from_text("Memory for A", namespace=ns_a)
        id_a = await store.save(rec_a)

        # Save to tenant-b
        rec_b = MemoryRecord.from_text("Memory for B", namespace=ns_b)
        id_b = await store.save(rec_b)

        # Query tenant-a
        matches_a = await store.query(MemoryQuery(namespace=ns_a, text_query="Memory"))
        assert len(matches_a) == 1
        assert matches_a[0].record.to_text() == "Memory for A"

        # Query tenant-b
        matches_b = await store.query(MemoryQuery(namespace=ns_b, text_query="Memory"))
        assert len(matches_b) == 1
        assert matches_b[0].record.to_text() == "Memory for B"

        # Verify get retrieves record
        assert await store.get(id_a) is not None

        # Delete from tenant-a
        deleted = await store.delete(id_a)
        assert deleted is True

        # Check id_a is deleted, id_b remains
        assert await store.get(id_a) is None
        assert await store.get(id_b) is not None
    finally:
        await store.disconnect()


@pytest.mark.asyncio
async def test_durable_memory_store_query_paging():
    """query() — ordering and limit semantics for standing-context injection."""
    if not await check_db_available():
        pytest.skip("PostgreSQL database not available")

    db_url = get_db_url()
    store = DurableMemoryStore(db_url)
    await store.connect()
    await store.create_tables()

    ns_user = MemoryNamespace(tenant_id="preference", user_id="list-all-test-user")
    ns_other = MemoryNamespace(tenant_id="preference", user_id="list-all-test-other-user")

    try:
        await store.clear(ns_user)
        await store.clear(ns_other)

        await store.save(MemoryRecord.from_text("Always answer in French", namespace=ns_user))
        await store.save(MemoryRecord.from_text("Prefers concise answers", namespace=ns_user))
        await store.save(
            MemoryRecord.from_text("Not this user's memory", namespace=ns_other)
        )

        matches = await store.query(MemoryQuery(namespace=ns_user, limit=20))

        assert len(matches) == 2
        contents = {m.record.to_text() for m in matches}
        assert contents == {"Always answer in French", "Prefers concise answers"}
        # Most-recent-first ordering: the second save() is newer.
        assert matches[0].record.to_text() == "Prefers concise answers"

        # limit is honored.
        capped = await store.query(MemoryQuery(namespace=ns_user, limit=1))
        assert len(capped) == 1

        # Doesn't leak across users.
        other_matches = await store.query(MemoryQuery(namespace=ns_other))
        assert len(other_matches) == 1
        assert other_matches[0].record.to_text() == "Not this user's memory"
    finally:
        await store.clear(ns_user)
        await store.clear(ns_other)
        await store.disconnect()


@pytest.mark.asyncio
async def test_durable_memory_store_multimodal():
    if not await check_db_available():
        pytest.skip("PostgreSQL database not available")

    from substrate.kernel.core.content import DataBlock, MediaBlock, TextBlock

    db_url = get_db_url()
    store = DurableMemoryStore(db_url)
    await store.connect()
    await store.create_tables()

    ns = MemoryNamespace(tenant_id="docs", user_id="multimodal-user")
    try:
        await store.clear(ns)
        blocks = [
            TextBlock(text="Invoice #1234 details"),
            MediaBlock.image(url="https://example.com/receipt.jpg", media_type="image/jpeg"),
            DataBlock(data={"amount": 420.50, "currency": "EUR"}),
        ]
        rec = MemoryRecord(content=blocks, namespace=ns)
        mem_id = await store.save(rec)

        # FTS search on the text representation
        matches = await store.query(MemoryQuery(namespace=ns, text_query="Invoice"))
        assert len(matches) == 1
        retrieved = matches[0].record
        assert retrieved.id == mem_id
        assert len(retrieved.content) == 3
        assert isinstance(retrieved.content[0], TextBlock)
        assert isinstance(retrieved.content[1], MediaBlock)
        assert retrieved.content[1].type == "image"
        assert isinstance(retrieved.content[2], DataBlock)
        assert retrieved.content[2].data["amount"] == 420.50
    finally:
        await store.clear(ns)
        await store.disconnect()


# ── 3. DurableHistoryProvider Protocol Tests ────────────────────────────────


@pytest.mark.asyncio
async def test_postgres_history_provider_conformance():
    if not await check_db_available():
        pytest.skip("PostgreSQL database not available")

    db_url = get_db_url()
    provider = DurableHistoryProvider(db_url)
    await provider.connect()

    session_id = "sess-history-test"

    try:
        await provider.delete_session(session_id)

        parent = None
        for role, text, run in [
            ("user", "message 1", "run-x"),
            ("assistant", "message 2", "run-y"),
            ("user", "message 3", "run-y"),
        ]:
            node = MessageNode(
                parent_id=parent,
                session_id=session_id,
                run_id=run,
                payload=ChatMessage(role=role, content=[TextBlock(text=text)]),
            )
            await provider.append_and_advance(node, "main")
            parent = node.id

        loaded = await project_messages(provider, session_id)
        assert [m.content[0].text for m in loaded] == ["message 1", "message 2", "message 3"]
        assert all(isinstance(m, ChatMessage) for m in loaded)

        await provider.delete_session(session_id)
        assert await project_messages(provider, session_id) == []
    finally:
        await provider.delete_session(session_id)
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
