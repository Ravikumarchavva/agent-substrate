"""ingest_session_document — real end-to-end integration test.

Runs actual extraction (pypdf fallback, no extraction service configured),
actual chunking/embedding into a real local LanceDBVectorStore, actual
PageIndex tree building into a real LanceLongTermMemory, and actual
"graph extraction" (LLM call stubbed, but the store write is real) into a
real LanceGraphStore — only the embedding/LLM network calls are stubbed,
matching this test file's sibling (test_rag_pipelines.py)'s existing
convention. Confirms the whole chain the storage plan describes actually
works together, not just each store in isolation.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from substrate.capabilities.knowledge.backends.local import LocalRagBackend
from substrate.capabilities.knowledge.pipeline import RAGPipeline
from substrate.capabilities.knowledge.session_ingest import ingest_session_document
from substrate.config import SubstrateConfig
from substrate.infrastructure.serving_factory import (
    build_page_index_memory,
    build_session_graph_store,
    build_session_index_vector_store,
)
from substrate.integrations.llm.openai.openai_embedding_client import (
    OpenAIEmbeddingClient,
)
from substrate.kernel import ChatMessage, TextBlock
from substrate.kernel.core.usage import Usage
from substrate.kernel.llm import EmbeddingResult, GenerationOptions, LLMResponse

FIXTURE_PDF = Path(__file__).parent.parent / "fixtures" / "test_invoice.pdf"


class StubLLMClient:
    def __init__(self, response_text: str) -> None:
        self._response_text = response_text
        self.calls: list[list[ChatMessage]] = []

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),
    ) -> LLMResponse:
        self.calls.append(messages)
        return LLMResponse(content=[TextBlock(text=self._response_text)], usage=Usage())


@pytest.fixture
def cfg(tmp_path) -> SubstrateConfig:
    return SubstrateConfig(SESSION_INDEX_LOCAL_PATH=str(tmp_path))


@pytest.fixture
def embedding_client() -> OpenAIEmbeddingClient:
    client = OpenAIEmbeddingClient(api_key="mock")
    client.embed = AsyncMock(
        side_effect=lambda texts, **kw: EmbeddingResult(
            embeddings=[[0.1] * 1536 for _ in texts], model="test"
        )
    )
    return client


@pytest.fixture
def local_rag_backend() -> LocalRagBackend:
    # No extraction_service_url -> forces the pypdf fallback extraction path
    # (_load_via_registry), which is what a plain dev/test environment
    # actually has available.
    dummy_pipeline = RAGPipeline(
        embedding_client=OpenAIEmbeddingClient(api_key="mock"), vector_store=None
    )
    return LocalRagBackend(dummy_pipeline)


async def test_ingest_session_document_writes_to_all_three_stores(
    cfg: SubstrateConfig,
    embedding_client: OpenAIEmbeddingClient,
    local_rag_backend: LocalRagBackend,
) -> None:
    data = FIXTURE_PDF.read_bytes()
    model_client = StubLLMClient(
        '{"entities": [{"label": "Company", "properties": {"name": "Acme"}}], '
        '"relationships": []}'
    )

    result = await ingest_session_document(
        data=data,
        filename="invoice.pdf",
        content_type="application/pdf",
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-1",
        cfg=cfg,
        embedding_client=embedding_client,
        model_client=model_client,
        rag_backend=local_rag_backend,
    )

    assert result.chunks > 0
    assert result.pageindex_nodes > 0
    assert result.graph_extraction_attempted is True

    # Independently re-open all three stores the same way the query side
    # will (fresh instances, not the ones ingest_session_document built
    # internally) and confirm the data really persisted.
    vector_store = build_session_index_vector_store(cfg, "tenant-a", "user-a")
    hits = await vector_store.search([0.1] * 1536, collection="vectors", limit=10)
    assert len(hits) == result.chunks
    assert all(h.metadata.get("session_id") == "session-1" for h in hits)

    from substrate.kernel.core.identity import ActorRole, Actor

    # PageIndexRAGPipeline's default agent_id ("system") when
    # ingest_session_document doesn't override it — see its constructor.
    memory = build_page_index_memory(cfg, "tenant-a", "user-a")
    memories = await memory.search(
        Actor(role=ActorRole.INTERNAL, id="system"), "documents", namespace="page_index_trees"
    )
    assert len(memories) == 1  # one collection root ("documents")

    graph_store = build_session_graph_store(cfg, "tenant-a", "user-a")
    # The stub LLM response above names one entity ("Acme") with no id, so
    # its auto-generated uuid isn't predictable — just confirm something
    # real landed via the narrow query_cypher path.
    rows = await graph_store.query_cypher("MATCH (n) RETURN n LIMIT 100")
    assert len(rows) == 1
