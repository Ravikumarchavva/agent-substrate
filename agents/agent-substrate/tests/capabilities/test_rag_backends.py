"""Tests for the pluggable RagBackend layer (local + factory)."""

from __future__ import annotations

from pathlib import Path

import pytest

from substrate.kernel.storage.vector import Document, SearchResult
from substrate.kernel.llm import EmbeddingResult, LLMResponse
from substrate.kernel.core.content import TextBlock
from substrate.capabilities.knowledge.backends import (
    RagBackendUnavailableError,
    build_rag_backend,
)
from substrate.capabilities.knowledge.backends.local import LocalRagBackend
from substrate.capabilities.knowledge.reranker import LLMReranker


class StubEmbeddingClient:
    """Deterministic stub EmbeddingClient — same shape as an OpenAI client."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        self.calls.append(texts)
        return EmbeddingResult(
            embeddings=[[0.1, 0.2, 0.3] for _ in texts], model="stub"
        )

    async def embed_single(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]


class StubVectorStore:
    """Same stub shape used in tests/capabilities/test_rag_pipelines.py."""

    def __init__(self) -> None:
        self.documents: list[Document] = []
        self.added_collections: list[str] = []

    async def add(
        self, documents: list[Document], *, collection: str = "default"
    ) -> list[str]:
        self.documents.extend(documents)
        self.added_collections.append(collection)
        return [doc.id for doc in documents]

    async def search(
        self,
        query_embedding: list[float],
        *,
        collection: str = "default",
        limit: int = 5,
        filter=None,
    ) -> list[SearchResult]:
        return [
            SearchResult(
                id=doc.id, content=doc.content, score=0.9, metadata=doc.metadata
            )
            for doc in self.documents[:limit]
        ]

    async def get(self, ids, *, collection: str = "default"):
        return []

    async def upsert(self, documents, *, collection: str = "default"):
        return []

    async def delete(self, ids, *, collection: str = "default") -> int:
        return 0

    async def list_collections(self) -> list[str]:
        return ["default"]

    async def delete_collection(self, collection: str) -> int:
        return len(self.documents)


class StubLLMClient:
    """Same stub shape used in tests/capabilities/test_rag_pipelines.py."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses

    async def generate(self, messages, *, options=None) -> LLMResponse:
        from substrate.kernel.core.usage import Usage

        text = self.responses.pop(0) if self.responses else "stub response"
        return LLMResponse(content=[TextBlock(text=text)], usage=Usage())


# ── chunk_size/chunk_overlap resolution (model-informed default) ───────────


def test_build_rag_backend_derives_chunk_size_from_embedding_model_when_unset():
    embed = StubEmbeddingClient()
    store = StubVectorStore()
    backend = build_rag_backend(
        "local",
        embedding_client=embed,
        vector_store=store,
        embedding_model="sentence-transformers/all-MiniLM-L6-v2",
    )
    assert backend._pipeline._default_chunk_size == 256 * 4
    assert backend._pipeline._default_chunk_overlap == (256 * 4) // 4


def test_build_rag_backend_explicit_chunk_size_wins_over_embedding_model():
    embed = StubEmbeddingClient()
    store = StubVectorStore()
    backend = build_rag_backend(
        "local",
        embedding_client=embed,
        vector_store=store,
        embedding_model="sentence-transformers/all-MiniLM-L6-v2",
        chunk_size=999,
        chunk_overlap=111,
    )
    assert backend._pipeline._default_chunk_size == 999
    assert backend._pipeline._default_chunk_overlap == 111


def test_build_rag_backend_with_no_embedding_model_still_works():
    embed = StubEmbeddingClient()
    store = StubVectorStore()
    backend = build_rag_backend("local", embedding_client=embed, vector_store=store)
    assert backend._pipeline._default_chunk_size > 0
    assert backend._pipeline._default_chunk_overlap > 0


# ── LocalRagBackend ─────────────────────────────────────────────────────────


async def test_local_backend_ingest_chunks_embeds_stores(tmp_path: Path):
    embed = StubEmbeddingClient()
    store = StubVectorStore()
    backend = build_rag_backend("local", embedding_client=embed, vector_store=store)
    assert backend.name == "local"

    doc = tmp_path / "notes.txt"
    doc.write_text("hello world, this is a test document.")

    result = await backend.ingest(str(doc), collection="kb")

    assert result.chunks_indexed == len(store.documents)
    assert result.chunks_indexed >= 1
    assert store.added_collections == ["kb"]


async def test_local_backend_ingest_bytes_with_filename_metadata():
    embed = StubEmbeddingClient()
    store = StubVectorStore()
    backend = build_rag_backend("local", embedding_client=embed, vector_store=store)

    result = await backend.ingest(
        b"raw text content", metadata={"filename": "upload.txt"}
    )

    assert result.chunks_indexed == 1
    assert store.documents[0].to_text() == "raw text content"


async def test_local_backend_query_returns_search_results():
    embed = StubEmbeddingClient()
    store = StubVectorStore()
    store.documents.append(Document.from_text("stored chunk"))
    backend = build_rag_backend("local", embedding_client=embed, vector_store=store)

    results = await backend.query("what is stored?", limit=5)

    assert len(results) == 1
    assert results[0].to_text() == "stored chunk"


async def test_local_backend_query_reranks_when_configured():
    embed = StubEmbeddingClient()
    store = StubVectorStore()
    store.documents.append(Document.from_text("chunk A"))
    store.documents.append(Document.from_text("chunk B"))
    store.documents.append(Document.from_text("chunk C"))
    # LLMReranker only calls the LLM when more candidates than top_k are
    # fetched (else it short-circuits) — fetch_limit=limit*3=6, store has 3,
    # top_k=2, so 3 > 2 candidates actually get reranked.
    llm = StubLLMClient(responses=["[2, 0]"])
    reranker = LLMReranker(llm)
    backend = LocalRagBackend(
        pipeline=__import__(
            "substrate.capabilities.knowledge.pipeline", fromlist=["RAGPipeline"]
        ).RAGPipeline(embed, store),
        vector_store=store,
        reranker=reranker,
    )

    results = await backend.query("query", limit=2)

    assert [r.to_text() for r in results] == ["chunk C", "chunk A"]


async def test_local_backend_query_with_context_requires_model_client():
    embed = StubEmbeddingClient()
    store = StubVectorStore()
    backend = build_rag_backend("local", embedding_client=embed, vector_store=store)

    with pytest.raises(RuntimeError, match="model_client"):
        await backend.query_with_context("question")


async def test_local_backend_query_with_context_uses_pipeline():
    embed = StubEmbeddingClient()
    store = StubVectorStore()
    store.documents.append(Document.from_text("chunk"))
    llm = StubLLMClient(responses=["the answer"])
    backend = build_rag_backend(
        "local", embedding_client=embed, vector_store=store, model_client=llm
    )

    answer = await backend.query_with_context("question")

    assert answer == "the answer"


async def test_local_backend_no_loader_raises_rag_load_error():
    embed = StubEmbeddingClient()
    store = StubVectorStore()
    backend = build_rag_backend("local", embedding_client=embed, vector_store=store)

    with pytest.raises(Exception, match="No local loader"):
        await backend.ingest(b"binary blob", metadata={"filename": "file.xyz"})


async def test_local_backend_list_and_delete_collections():
    embed = StubEmbeddingClient()
    store = StubVectorStore()
    backend = build_rag_backend("local", embedding_client=embed, vector_store=store)

    assert await backend.list_collections() == ["default"]
    assert await backend.delete_collection("default") == 0


# ── Factory ─────────────────────────────────────────────────────────────────


async def test_factory_unknown_kind_raises():
    with pytest.raises(RagBackendUnavailableError, match="Unknown RAG_BACKEND"):
        build_rag_backend("bogus")


async def test_factory_local_missing_kwargs_raises():
    with pytest.raises(RagBackendUnavailableError, match="requires embedding_client"):
        build_rag_backend("local")
