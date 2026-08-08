"""Tests for the pluggable RagBackend layer (local + pinecone + factory)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

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


# ── PineconeRagBackend ──────────────────────────────────────────────────────
# Patches the real installed `pinecone` SDK's `Pinecone` client class (the
# `rag-pinecone` extra is a real dependency here, not faked) — verified
# against pinecone==9.1.0's actual shape: `pc.assistants.describe/create`
# returning an `AssistantModel` with `.upload_file`/`.context`/`.chat`.

pinecone = pytest.importorskip("pinecone")


@pytest.fixture
def fake_pinecone_client(monkeypatch):
    assistant = MagicMock()
    client = MagicMock()
    client.assistants.describe = MagicMock(return_value=assistant)
    monkeypatch.setattr(pinecone, "Pinecone", MagicMock(return_value=client))
    return assistant


async def test_pinecone_backend_ingest_uploads_file(fake_pinecone_client, tmp_path):
    from substrate.capabilities.knowledge.backends.pinecone import PineconeRagBackend

    file_ref = MagicMock(id="file-123")
    fake_pinecone_client.upload_file = MagicMock(return_value=file_ref)

    backend = PineconeRagBackend(api_key="key", assistant_name="docs")
    doc = tmp_path / "notes.txt"
    doc.write_text("content")

    result = await backend.ingest(str(doc), collection="thread-1")

    assert result.chunks_indexed == -1
    assert result.document_id == "file-123"
    _, kwargs = fake_pinecone_client.upload_file.call_args
    assert kwargs["metadata"]["collection"] == "thread-1"
    # multimodal=True — without it Pinecone only OCRs/reads plain text
    # layers, silently dropping scanned-image page content.
    assert kwargs["multimodal"] is True


async def test_pinecone_backend_query_maps_snippets_and_filters_by_collection(
    fake_pinecone_client,
):
    from substrate.capabilities.knowledge.backends.pinecone import PineconeRagBackend

    snippet = MagicMock()
    snippet.content = "relevant passage"
    snippet.score = 0.87
    snippet.reference.file.id = "file-123"
    response = MagicMock(snippets=[snippet])
    fake_pinecone_client.context = MagicMock(return_value=response)

    backend = PineconeRagBackend(api_key="key", assistant_name="docs")
    results = await backend.query("question", collection="thread-1", limit=3)

    assert len(results) == 1
    assert results[0].id == "file-123"
    assert results[0].score == 0.87
    assert results[0].to_text() == "relevant passage"
    _, kwargs = fake_pinecone_client.context.call_args
    assert kwargs["filter"] == {"collection": {"$eq": "thread-1"}}


async def test_pinecone_backend_query_normalizes_citation_metadata(
    fake_pinecone_client,
):
    """SearchResult.metadata must carry filename/pages/page_number and echo
    back custom ingest metadata (file_id, session_path) — this is what
    capabilities/knowledge/citations.py builds citations from."""
    from substrate.capabilities.knowledge.backends.pinecone import PineconeRagBackend

    snippet = MagicMock()
    snippet.content = "relevant passage"
    snippet.score = 0.87
    snippet.reference.pages = [7]
    snippet.reference.file.id = "pinecone-file-id"
    snippet.reference.file.name = "tmpabc.pdf"
    snippet.reference.file.metadata = {
        "collection": "thread-1",
        "content_type": "application/pdf",
        "filename": "Naac_appLetter.pdf",
        "file_id": "db-file-id",
        "session_path": "Naac_appLetter.pdf",
    }
    response = MagicMock(snippets=[snippet])
    fake_pinecone_client.context = MagicMock(return_value=response)

    backend = PineconeRagBackend(api_key="key", assistant_name="docs")
    results = await backend.query("question", collection="thread-1", limit=3)

    metadata = results[0].metadata
    assert metadata["filename"] == "Naac_appLetter.pdf"
    assert metadata["file_id"] == "db-file-id"
    assert metadata["session_path"] == "Naac_appLetter.pdf"
    assert metadata["pages"] == [7]
    assert metadata["page_number"] == 7
    assert metadata["pinecone_file_id"] == "pinecone-file-id"


async def test_pinecone_backend_query_metadata_falls_back_to_file_name(
    fake_pinecone_client,
):
    """When no custom `filename` was set at ingest, fall back to Pinecone's
    own file name rather than leaving the citation unlabelled."""
    from substrate.capabilities.knowledge.backends.pinecone import PineconeRagBackend

    snippet = MagicMock()
    snippet.content = "text"
    snippet.score = 0.5
    snippet.reference.pages = [1, 2, 3]
    snippet.reference.file.id = "f1"
    snippet.reference.file.name = "raw_upload.pdf"
    snippet.reference.file.metadata = {}
    fake_pinecone_client.context = MagicMock(return_value=MagicMock(snippets=[snippet]))

    backend = PineconeRagBackend(api_key="key", assistant_name="docs")
    results = await backend.query("question")

    assert results[0].metadata["filename"] == "raw_upload.pdf"
    assert results[0].metadata["pages"] == [1, 2, 3]
    assert results[0].metadata["page_number"] == 1


async def test_pinecone_backend_query_missing_reference_yields_empty_metadata(
    fake_pinecone_client,
):
    """A snippet with no reference (or a reshaped future SDK response) must
    degrade to empty metadata, never raise — a citation is a nice-to-have on
    top of a working answer."""
    from substrate.capabilities.knowledge.backends.pinecone import PineconeRagBackend

    snippet = MagicMock()
    snippet.content = "text"
    snippet.score = 0.5
    snippet.reference = None
    fake_pinecone_client.context = MagicMock(return_value=MagicMock(snippets=[snippet]))

    backend = PineconeRagBackend(api_key="key", assistant_name="docs")
    results = await backend.query("question")

    assert results[0].metadata == {}


async def test_pinecone_backend_query_with_context_chats(fake_pinecone_client):
    from substrate.capabilities.knowledge.backends.pinecone import PineconeRagBackend

    response = MagicMock()
    response.message.content = "assistant answer"
    fake_pinecone_client.chat = MagicMock(return_value=response)

    backend = PineconeRagBackend(api_key="key", assistant_name="docs")
    answer = await backend.query_with_context("question", collection="thread-1")

    assert answer == "assistant answer"
    _, kwargs = fake_pinecone_client.chat.call_args
    assert kwargs["filter"] == {"collection": {"$eq": "thread-1"}}


async def test_pinecone_backend_creates_assistant_when_missing(monkeypatch):
    from substrate.capabilities.knowledge.backends.pinecone import PineconeRagBackend

    client = MagicMock()
    client.assistants.describe = MagicMock(
        side_effect=pinecone.exceptions.NotFoundException()
    )
    created = MagicMock()
    client.assistants.create = MagicMock(return_value=created)
    monkeypatch.setattr(pinecone, "Pinecone", MagicMock(return_value=client))

    backend = PineconeRagBackend(api_key="key", assistant_name="new-assistant")

    client.assistants.create.assert_called_once_with(name="new-assistant")
    assert backend._assistant is created


async def test_pinecone_backend_missing_sdk_raises_unavailable(monkeypatch):
    import builtins

    from substrate.capabilities.knowledge.backends import pinecone as pinecone_backend

    real_import = builtins.__import__

    def _blocked_import(name, *args, **kwargs):
        if name == "pinecone":
            raise ImportError("no pinecone")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)

    with pytest.raises(
        RagBackendUnavailableError, match="requires the 'pinecone' package"
    ):
        pinecone_backend.PineconeRagBackend(api_key="key", assistant_name="docs")


async def test_pinecone_backend_missing_api_key_raises_unavailable(
    fake_pinecone_client,
):
    from substrate.capabilities.knowledge.backends.pinecone import PineconeRagBackend

    with pytest.raises(RagBackendUnavailableError, match="requires an api_key"):
        PineconeRagBackend(api_key="", assistant_name="docs")


# ── Factory ─────────────────────────────────────────────────────────────────


async def test_factory_unknown_kind_raises():
    with pytest.raises(RagBackendUnavailableError, match="Unknown RAG_BACKEND"):
        build_rag_backend("bogus")


async def test_factory_local_missing_kwargs_raises():
    with pytest.raises(RagBackendUnavailableError, match="requires embedding_client"):
        build_rag_backend("local")


async def test_factory_pinecone_missing_assistant_name_raises():
    with pytest.raises(RagBackendUnavailableError, match="requires assistant_name"):
        build_rag_backend("pinecone", api_key="key")
