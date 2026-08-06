"""Tests for KnowledgeSearchTool — thin wrapper around a RagBackend."""

from __future__ import annotations

from substrate.capabilities.knowledge.backends.base import IngestResult
from substrate.capabilities.tools.ai.knowledge_search import KnowledgeSearchTool
from substrate.kernel.core.content import TextBlock
from substrate.kernel.storage.vector import SearchResult


class FakeRagBackend:
    name = "fake"

    def __init__(self) -> None:
        self.ingest_calls: list[tuple] = []
        self.query_calls: list[tuple] = []

    async def ingest(
        self, source, *, collection="default", metadata=None
    ) -> IngestResult:
        self.ingest_calls.append((source, collection, metadata))
        return IngestResult(chunks_indexed=3)

    async def query(self, question, *, collection="default", limit=5):
        self.query_calls.append((question, collection, limit))
        return [
            SearchResult(
                id="1",
                content=[TextBlock(text="relevant text")],
                score=0.75,
                metadata={},
            )
        ]

    async def query_with_context(
        self, question, *, collection="default", limit=5
    ) -> str:
        return "generated answer"


async def test_knowledge_search_tool_search_calls_backend_query():
    backend = FakeRagBackend()
    tool = KnowledgeSearchTool(backend)

    result = await tool.execute(action="search", text="what is X?", limit=3)

    assert backend.query_calls == [("what is X?", "default", 3)]
    assert not result.is_error
    assert "relevant text" in result.content[0].text


async def test_knowledge_search_tool_ingest_calls_backend_ingest():
    backend = FakeRagBackend()
    tool = KnowledgeSearchTool(backend)

    result = await tool.execute(action="ingest", text="some document text")

    assert backend.ingest_calls == [("some document text", "default", None)]
    assert not result.is_error
    assert "3 chunks" in result.content[0].text


async def test_knowledge_search_tool_requires_text():
    backend = FakeRagBackend()
    tool = KnowledgeSearchTool(backend)

    result = await tool.execute(action="search", text="")

    assert result.is_error


async def test_knowledge_search_tool_no_results():
    class EmptyBackend(FakeRagBackend):
        async def query(self, question, *, collection="default", limit=5):
            return []

    tool = KnowledgeSearchTool(EmptyBackend())
    result = await tool.execute(action="search", text="anything")

    assert "No matching documents" in result.content[0].text


async def test_knowledge_search_tool_unknown_action():
    tool = KnowledgeSearchTool(FakeRagBackend())
    result = await tool.execute(action="bogus", text="x")

    assert result.is_error
