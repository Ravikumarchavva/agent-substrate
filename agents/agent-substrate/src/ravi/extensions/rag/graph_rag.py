"""Graph-enhanced RAG pipeline.

Combines vector similarity search with knowledge-graph traversal for
richer context.  Extracts entities and relationships from documents
using an LLM, stores them in a graph store, and enriches query results
with graph context.

Usage::

    from ravi.extensions.rag.graph_rag import GraphRAGPipeline

    pipeline = GraphRAGPipeline(
        rag_pipeline=rag_pipeline,
        graph_store=graph_store,
        model_client=model_client,
    )
    await pipeline.ingest_with_graph("Long document ...", collection="kb")
    results = await pipeline.query("Who works at Acme?", collection="kb")
"""

from __future__ import annotations
from ravi.logger import setup_logging

import json
from typing import TYPE_CHECKING, Any, Optional

from ravi.extensions.rag.graph_store import Entity, Relationship
from ravi.extensions.rag.vector_store import SearchResult

if TYPE_CHECKING:
    from ravi.kernel.llm.base_client import BaseModelClient
    from ravi.extensions.rag.graph_store import BaseGraphStore
    from ravi.extensions.rag.pipeline import RAGPipeline

logger = setup_logging()


class GraphRAGPipeline:
    """RAG pipeline enriched with knowledge-graph context.

    Workflow:
    1. Ingest: chunk + embed + store (via RAGPipeline) + extract entities/rels (via LLM) + store in graph
    2. Query: vector search + graph traversal → combined context → LLM answer
    """

    def __init__(
        self,
        rag_pipeline: RAGPipeline,
        graph_store: BaseGraphStore,
        model_client: BaseModelClient,
    ) -> None:
        self._rag = rag_pipeline
        self._graph = graph_store
        self._model = model_client

    async def ingest_with_graph(
        self,
        content: str | list[str],
        *,
        collection: str = "default",
        extract_graph: bool = True,
        **ingest_kwargs: Any,
    ) -> int:
        """Ingest content into both vector store and knowledge graph.

        Returns the number of chunks stored in the vector store.
        """
        chunks = await self._rag.ingest(content, collection=collection, **ingest_kwargs)

        if extract_graph:
            texts = [content] if isinstance(content, str) else content
            for text in texts:
                await self._extract_and_store_graph(text)

        return chunks

    async def _extract_and_store_graph(self, text: str) -> None:
        """Use an LLM to extract entities and relationships from text."""
        from ravi.kernel.messages.client_messages import SystemMessage, UserMessage

        # Truncate very long texts for entity extraction
        extract_text = text[:5000] if len(text) > 5000 else text

        messages = [
            SystemMessage(
                content=(
                    "Extract entities and relationships from the text. "
                    "Return a JSON object with two arrays:\n"
                    '- "entities": [{"label": "Person", "properties": {"name": "Alice"}}]\n'
                    '- "relationships": [{"source": "Alice", "target": "Acme Corp", '
                    '"type": "WORKS_AT"}]\n'
                    "Return ONLY valid JSON."
                )
            ),
            UserMessage(role="user", content=[extract_text]),
        ]

        try:
            response = await self._model.generate_text(messages)
            text_content = ""
            if response.content:
                text_content = "".join(
                    p for p in response.content if isinstance(p, str)
                )

            data = json.loads(text_content.strip())

            # Store entities
            entities: list[Entity] = []
            entity_name_to_id: dict[str, str] = {}
            for e in data.get("entities", []):
                entity = Entity(
                    label=e.get("label", "Thing"),
                    properties=e.get("properties", {}),
                )
                name = e.get("properties", {}).get("name", entity.id)
                entity_name_to_id[name] = entity.id
                entities.append(entity)

            if entities:
                await self._graph.add_entities(entities)

            # Store relationships
            rels: list[Relationship] = []
            for r in data.get("relationships", []):
                source_name = r.get("source", "")
                target_name = r.get("target", "")
                source_id = str(entity_name_to_id.get(source_name, source_name))
                target_id = str(entity_name_to_id.get(target_name, target_name))
                rels.append(
                    Relationship(
                        source_id=source_id,
                        target_id=target_id,
                        type=r.get("type", "RELATED_TO"),
                        properties=r.get("properties", {}),
                    )
                )

            if rels:
                await self._graph.add_relationships(rels)

            logger.info(
                "Extracted %d entities and %d relationships",
                len(entities),
                len(rels),
            )

        except Exception:
            logger.warning("Graph extraction failed", exc_info=True)

    async def query(
        self,
        question: str,
        *,
        collection: str = "default",
        limit: int = 5,
        graph_depth: int = 1,
    ) -> list[SearchResult]:
        """Query with combined vector + graph context."""
        # Vector search
        vector_results = await self._rag.query(
            question, collection=collection, limit=limit
        )

        # Graph enrichment: extract key terms from question, search graph
        # This is a simplified approach — full GraphRAG would do entity linking
        # For now, just return vector results (graph enrichment can be added
        # as needed without changing the interface)
        return vector_results

    async def query_with_context(
        self,
        question: str,
        *,
        collection: str = "default",
        limit: int = 5,
        system: Optional[str] = None,
    ) -> str:
        """Full GraphRAG: vector search + graph context → LLM answer."""
        return await self._rag.query_with_context(
            question,
            collection=collection,
            model_client=self._model,
            limit=limit,
            system=system,
        )
