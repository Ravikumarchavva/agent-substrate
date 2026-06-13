"""Graph-enhanced RAG pipeline.

Combines vector similarity search with knowledge-graph traversal for
richer context.  Extracts entities and relationships from documents
using an LLM, stores them in a graph store, and enriches query results
with graph context.

Usage::

    from ravi.capabilities.knowledge.graph_rag import GraphRAGPipeline

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
from typing import TYPE_CHECKING, Any

from ravi.kernel.storage.graph import Entity, Relationship
from ravi.kernel.storage.vector import SearchResult

if TYPE_CHECKING:
    from ravi.kernel.llm import LLMClient
    from ravi.kernel.storage.graph import GraphStore
    from ravi.capabilities.knowledge.pipeline import RAGPipeline

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
        graph_store: GraphStore,
        model_client: LLMClient,
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
        from ravi.kernel import ChatMessage, TextBlock

        # Truncate very long texts for entity extraction
        extract_text = text[:5000] if len(text) > 5000 else text

        messages = [
            ChatMessage(role="user", content=[TextBlock(text=extract_text)]),
        ]

        try:
            from ravi.kernel.llm import GenerationOptions

            response = await self._model.generate(
                messages,
                options=GenerationOptions(
                    system_instructions=(
                        "Extract entities and relationships from the text. "
                        "Return a JSON object with two arrays:\n"
                        '- "entities": [{"label": "Person", "properties": {"name": "Alice"}}]\n'
                        '- "relationships": [{"source": "Alice", "target": "Acme Corp", '
                        '"type": "WORKS_AT"}]\n'
                        "Return ONLY valid JSON."
                    )
                ),
            )
            text_parts = [b.text for b in response.content if isinstance(b, TextBlock)]
            text_content = "".join(text_parts)

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
        # 1. Vector search
        vector_results = await self._rag.query(
            question, collection=collection, limit=limit
        )

        # 2. Graph enrichment: extract potential entities from question / vector results
        words = [w.strip("?,.!:;()\"'") for w in question.split()]
        keywords = {w.lower() for w in words if len(w) > 3}

        # Also extract words from the vector search hits to enrich
        for r in vector_results:
            for word in r.to_text().split()[:50]:  # Look at the beginning of chunks
                w = word.strip("?,.!:;()\"'")
                if len(w) > 4 and w[0].isupper():
                    keywords.add(w.lower())

        # If CypherCapable, query the graph to find matching entities
        from ravi.kernel.storage.graph import CypherCapable

        matched_entities = []

        if isinstance(self._graph, CypherCapable):
            try:
                # Retrieve all nodes (limit to 100) to find matches in Python
                rows = await self._graph.query_cypher("MATCH (n) RETURN n LIMIT 100")
                for row in rows:
                    for val in row.values():
                        if isinstance(val, str):
                            try:
                                data = json.loads(val)
                                eid = data.get("_id", str(data.get("id", "")))
                                label = data.get("label", "Unknown")
                                props = {
                                    k: v
                                    for k, v in data.items()
                                    if k not in ("id", "_id", "label")
                                }
                                name = props.get("name", "").lower() or eid.lower()
                                if any(kw in name for kw in keywords):
                                    from ravi.kernel.storage.graph import Entity

                                    matched_entities.append(
                                        Entity(id=eid, label=label, properties=props)
                                    )
                            except Exception:
                                pass
            except Exception:
                logger.warning(
                    "Failed to query Cypher for entity matches", exc_info=True
                )

        # Retrieve neighbors of matched entities
        relationships_found = []
        entities_found = set()

        for entity in matched_entities[
            :5
        ]:  # Limit to top 5 matches to avoid context bloat
            subgraph = await self._graph.get_neighbors(entity.id, depth=graph_depth)
            for e in subgraph.entities:
                entities_found.add(f"{e.label}({e.properties.get('name', e.id)})")
            for r in subgraph.relationships:
                relationships_found.append(f"{r.source_id} -[{r.type}]-> {r.target_id}")

        if entities_found or relationships_found:
            graph_text = "Knowledge Graph Context:\n"
            if entities_found:
                graph_text += f"- Related Entities: {', '.join(entities_found)}\n"
            if relationships_found:
                graph_text += "- Relationships:\n" + "\n".join(
                    f"  * {rel}" for rel in relationships_found[:10]
                )

            # Append graph SearchResult
            from ravi.kernel import TextBlock

            graph_result = SearchResult(
                id="graph_context",
                content=[TextBlock(text=graph_text)],
                score=1.0,
                metadata={"source": "knowledge_graph"},
            )
            return [*vector_results, graph_result]

        return vector_results

    async def query_with_context(
        self,
        question: str,
        *,
        collection: str = "default",
        limit: int = 5,
        system: str | None = None,
        graph_depth: int = 1,
    ) -> str:
        """Full GraphRAG: vector search + graph context → LLM answer."""
        from ravi.kernel import ChatMessage, TextBlock

        results = await self.query(
            question,
            collection=collection,
            limit=limit,
            graph_depth=graph_depth,
        )

        # Build context block
        context_parts: list[str] = []
        for i, r in enumerate(results, 1):
            context_parts.append(f"[{i}] {r.to_text()}")
        context_block = "\n\n".join(context_parts)

        system_prompt = system or (
            "You are a helpful assistant. Answer the user's question using "
            "the provided vector context and knowledge graph context."
        )

        messages = [
            ChatMessage(role="user", content=[TextBlock(text=question)]),
        ]

        from ravi.kernel.llm import GenerationOptions

        response = await self._model.generate(
            messages,
            options=GenerationOptions(
                system_instructions=f"{system_prompt}\n\nContext:\n{context_block}"
            ),
        )

        # Extract text from response blocks
        text_parts = [b.text for b in response.content if isinstance(b, TextBlock)]
        return "".join(text_parts)
