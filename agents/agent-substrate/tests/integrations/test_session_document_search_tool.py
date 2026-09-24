"""SessionDocumentSearchTool — real end-to-end: ingest a real PDF via
ingest_session_document, then query it back through the tool in all three
modes, with ContextVars set the same way ReActAgent._handle_message() sets
them for a real chat turn.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from substrate.integrations.knowledge.backends.local import LocalRagBackend
from substrate.integrations.knowledge.pipeline import RAGPipeline
from substrate.integrations.knowledge.session_ingest import ingest_session_document
from substrate.integrations.tools.ai.session_document_search import (
    SessionDocumentSearchTool,
)
from substrate.config import SubstrateConfig
from substrate.integrations.llm.openai.openai_embedding_client import (
    OpenAIEmbeddingClient,
)
from substrate.kernel import ChatMessage, TextBlock
from substrate.kernel.agent.runtime_context import RunMeta, RunScope
from substrate.kernel.core.usage import Usage
from substrate.kernel.llm import EmbeddingResult, GenerationOptions, LLMResponse

FIXTURE_PDF = Path(__file__).parent.parent / "fixtures" / "test_invoice.pdf"


class StubLLMClient:
    def __init__(self, response_text: str) -> None:
        self._response_text = response_text

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),
    ) -> LLMResponse:
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


def _ctx(scope: RunScope | None = None) -> RunMeta:
    """The run context ReActAgent._handle_message() would have built."""
    from substrate.agents.runtime.cancellation import CancellationToken

    return RunMeta(
        run_id="run-1",
        cancellation=CancellationToken(),
        scope=scope or RunScope(tenant_id="tenant-a", user_id="user-a", thread_id="session-1"),
    )


async def _ingest_fixture(cfg, embedding_client) -> None:
    dummy_pipeline = RAGPipeline(
        embedding_client=OpenAIEmbeddingClient(api_key="mock"), vector_store=None
    )
    rag_backend = LocalRagBackend(dummy_pipeline)
    model_client = StubLLMClient(
        '{"entities": [{"label": "Company", "properties": {"name": "Acme"}}], '
        '"relationships": []}'
    )
    await ingest_session_document(
        data=FIXTURE_PDF.read_bytes(),
        filename="invoice.pdf",
        content_type="application/pdf",
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-1",
        cfg=cfg,
        embedding_client=embedding_client,
        model_client=model_client,
        rag_backend=rag_backend,
    )


async def test_vector_mode_finds_ingested_document(cfg, embedding_client) -> None:
    await _ingest_fixture(cfg, embedding_client)
    tool = SessionDocumentSearchTool(cfg, embedding_client, StubLLMClient("{}"))

    result = await tool.execute(ctx=_ctx(), query="invoice", mode="vector")

    assert not result.is_error
    text = result.content[0].text
    assert "invoice.pdf" in text


async def test_tree_mode_finds_document_outline(cfg, embedding_client) -> None:
    await _ingest_fixture(cfg, embedding_client)
    tool = SessionDocumentSearchTool(cfg, embedding_client, StubLLMClient("{}"))

    result = await tool.execute(ctx=_ctx(), query="invoice", mode="tree")

    assert not result.is_error
    assert "No matching" not in result.content[0].text


async def test_graph_mode_returns_vector_results_at_minimum(cfg, embedding_client) -> None:
    await _ingest_fixture(cfg, embedding_client)
    tool = SessionDocumentSearchTool(cfg, embedding_client, StubLLMClient("{}"))

    result = await tool.execute(ctx=_ctx(), query="invoice", mode="graph")

    assert not result.is_error
    assert "invoice.pdf" in result.content[0].text


async def test_missing_query_is_an_error(cfg, embedding_client) -> None:
    tool = SessionDocumentSearchTool(cfg, embedding_client, StubLLMClient("{}"))
    result = await tool.execute(ctx=_ctx(), query="")
    assert result.is_error


async def test_no_scope_without_a_signed_in_context(cfg, embedding_client) -> None:
    tool = SessionDocumentSearchTool(cfg, embedding_client, StubLLMClient("{}"))
    result = await tool.execute(ctx=_ctx(RunScope()), query="anything")
    assert result.is_error


async def test_omitted_limit_uses_cfg_rag_final_k(cfg, embedding_client) -> None:
    """Mirrors config.py's RAG_FINAL_K -- a caller omitting `limit` entirely
    should get the configured default, not a hardcoded 5."""
    cfg.RAG_FINAL_K = 17
    tool = SessionDocumentSearchTool(cfg, embedding_client, StubLLMClient("{}"))
    captured = {}

    async def _fake_search_vector(tenant_id, user_id, query, *, limit, filter=None):
        captured["limit"] = limit
        return []

    tool._search_vector = _fake_search_vector
    await tool.execute(ctx=_ctx(), query="anything")

    assert captured["limit"] == 17
