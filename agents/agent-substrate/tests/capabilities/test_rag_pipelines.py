import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from substrate.kernel import ChatMessage, TextBlock
from substrate.kernel.llm import LLMResponse, EmbeddingResult, GenerationOptions
from substrate.kernel.storage.vector import Document, SearchResult
from substrate.kernel.storage.graph import Entity, Relationship, SubGraph
from substrate.integrations.llm.openai.openai_embedding_client import (
    OpenAIEmbeddingClient,
)
from substrate.capabilities.knowledge.page_pipeline import PageIndexRAGPipeline
from substrate.capabilities.knowledge.graph_rag import GraphRAGPipeline
from substrate.capabilities.knowledge.pipeline import RAGPipeline


class StubLLMClient:
    """A stub LLMClient that returns pre-configured responses."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls = []

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),
    ) -> LLMResponse:
        self.calls.append((messages, options))
        text = self.responses.pop(0) if self.responses else "stub response"
        from substrate.kernel.core.usage import Usage

        return LLMResponse(content=[TextBlock(text=text)], usage=Usage())


class StubVectorStore:
    """A stub VectorStore to test ingestion and retrieval."""

    def __init__(self) -> None:
        self.documents = []

    async def add(
        self, documents: list[Document], *, collection: str = "default"
    ) -> list[str]:
        self.documents.extend(documents)
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


class StubGraphStore:
    """A stub GraphStore to test GraphRAG."""

    def __init__(self) -> None:
        self.entities = []
        self.relationships = []

    async def add_entities(self, entities: list[Entity]) -> list[str]:
        self.entities.extend(entities)
        return [e.id for e in entities]

    async def add_relationships(self, relationships: list[Relationship]) -> list[str]:
        self.relationships.extend(relationships)
        return [r.id for r in relationships]

    async def get_neighbors(
        self, entity_id: str, *, depth: int = 1, relationship_types=None
    ) -> SubGraph:
        # Return all entities and relationships as a subgraph for testing
        return SubGraph(
            entities=tuple(self.entities),
            relationships=tuple(self.relationships),
        )

    async def query_cypher(self, query: str, params=None) -> list[dict]:
        # Return entities serialized as agtype JSON strings
        return [
            {
                "n": json.dumps(
                    {
                        "id": e.id,
                        "_id": e.id,
                        "label": e.label,
                        "name": e.properties.get("name", ""),
                    }
                )
            }
            for e in self.entities
        ]


@pytest.mark.asyncio
async def test_openai_embedding_client():
    # Mock AsyncOpenAI
    mock_client = MagicMock()
    mock_embeddings = MagicMock()
    mock_client.embeddings = mock_embeddings

    # Setup mock return value for response.data
    mock_data_item = MagicMock()
    mock_data_item.embedding = [0.1, 0.2, 0.3]
    mock_response = MagicMock()
    mock_response.data = [mock_data_item]
    mock_response.usage.total_tokens = 5
    mock_embeddings.create = AsyncMock(return_value=mock_response)

    client = OpenAIEmbeddingClient(api_key="test", base_url="http://localhost:8000/v1")
    client.client = mock_client

    result = await client.embed(["hello"])
    assert result.embeddings == [[0.1, 0.2, 0.3]]
    assert result.usage_tokens == 5


@pytest.mark.asyncio
async def test_page_index_flat_traversal():
    # Setup mock LLM answers:
    # 1. Answer index '0' to navigate to Page 1 (first query)
    # 2. Answer 'retrieve' to stop at Page 1 (first query)
    # 3. Answer index '0' (second query in query_with_context)
    # 4. Answer 'retrieve' (second query in query_with_context)
    # 5. Final generation answer
    stub_llm = StubLLMClient(
        responses=["0", "retrieve", "0", "retrieve", "Navigated response"]
    )

    pipeline = PageIndexRAGPipeline(model_client=stub_llm)

    # Ingest a page-structured document
    await pipeline.ingest(
        content=["Page 1 content here", "Page 2 content here"],
        collection="test_kb",
        title="Document A",
        strategy="flat",
    )

    # Validate tree structure was built correctly
    tree = await pipeline._get_collection_tree("test_kb")
    assert len(tree.children) == 1
    assert tree.children[0].title == "Document A"
    assert len(tree.children[0].children) == 2  # Page 1 and Page 2

    # Query with traversal reasoning
    answers = await pipeline.query(
        "What is the first page about?", collection="test_kb"
    )
    assert len(answers) == 1
    assert "Page 1 content here" in answers[0].to_text()

    # Query with final answer generation
    final_answer = await pipeline.query_with_context(
        "What is the first page about?", collection="test_kb"
    )
    assert final_answer == "Navigated response"


@pytest.mark.asyncio
async def test_page_index_markdown_headers():
    stub_llm = StubLLMClient(responses=["retrieve"])
    pipeline = PageIndexRAGPipeline(model_client=stub_llm)

    markdown_text = """# Introduction
This is intro text.
## Details
More details here.
# Conclusion
This is the end.
"""
    await pipeline.ingest(
        content=markdown_text,
        collection="markdown_kb",
        title="Doc Markdown",
        strategy="markdown",
    )

    tree = await pipeline._get_collection_tree("markdown_kb")
    doc_node = tree.children[0]
    assert len(doc_node.children) == 2  # Introduction, Conclusion
    assert doc_node.children[0].children[0].title == "Details"
    assert doc_node.children[0].children[0].content == "More details here."


@pytest.mark.asyncio
async def test_graph_rag_enrichment():
    # Setup mock LLM for Graph extraction
    # Returns entity/rel JSON
    extraction_json = json.dumps(
        {
            "entities": [{"label": "Person", "properties": {"name": "Alice"}}],
            "relationships": [
                {"source": "Alice", "target": "Acme", "type": "WORKS_AT"}
            ],
        }
    )
    stub_llm = StubLLMClient(responses=[extraction_json, "Final Graph RAG answer"])

    # Setup stubs
    embed_client = OpenAIEmbeddingClient(api_key="mock")
    embed_client.embed = AsyncMock(
        return_value=EmbeddingResult(embeddings=[[0.1] * 1536], model="test")
    )
    embed_client.embed_single = AsyncMock(return_value=[0.1] * 1536)

    vector_store = StubVectorStore()
    graph_store = StubGraphStore()

    rag_pipeline = RAGPipeline(embedding_client=embed_client, vector_store=vector_store)

    graph_rag = GraphRAGPipeline(
        rag_pipeline=rag_pipeline,
        graph_store=graph_store,
        model_client=stub_llm,
    )

    # Ingest document and extract graph
    await graph_rag.ingest_with_graph(
        "Alice works at Acme Corporation.",
        collection="graph_kb",
    )

    assert len(graph_store.entities) == 1
    assert graph_store.entities[0].properties["name"] == "Alice"

    # Query Graph RAG - should perform vector search and enrich with graph connections
    results = await graph_rag.query("Where does Alice work?", collection="graph_kb")

    # The result list should contain vector search matches + the graph context result
    assert len(results) > 1
    graph_results = [r for r in results if r.id == "graph_context"]
    assert len(graph_results) == 1
    assert "Alice" in graph_results[0].to_text()

    # Full GraphRAG response generation
    answer = await graph_rag.query_with_context(
        "Where does Alice work?", collection="graph_kb"
    )
    assert answer == "Final Graph RAG answer"
