"""RAG Pipeline — end-to-end ingest + query orchestrator.

Wires together an embedding client, vector store, and chunker into a
single high-level interface.

Usage::

    from agent_substrate.capabilities.knowledge.pipeline import RAGPipeline

    pipeline = RAGPipeline(
        embedding_client=embed_client,
        vector_store=vector_store,
    )
    await pipeline.ingest("Long doc ...", collection="kb")
    results = await pipeline.query("What is X?", collection="kb")
"""

from __future__ import annotations
import dataclasses
from agent_substrate.logger import setup_logging

from typing import TYPE_CHECKING, Any

from agent_substrate.capabilities.knowledge.chunking import get_chunker
from agent_substrate.kernel.storage.vector import Document, SearchResult, VectorStore

if TYPE_CHECKING:
    from agent_substrate.kernel.llm import LLMClient, EmbeddingClient as BaseEmbeddingClient

logger = setup_logging()


class RAGPipeline:
    """End-to-end Retrieval-Augmented Generation pipeline.

    Handles: chunk → embed → store (ingest) and embed → search (query).
    Optionally generates an answer using a model client (query_with_context).
    """

    def __init__(
        self,
        embedding_client: BaseEmbeddingClient,
        vector_store: VectorStore,
        default_chunk_size: int = 512,
        default_chunk_overlap: int = 128,
    ) -> None:
        self._embedding = embedding_client
        self._store = vector_store
        self._default_chunk_size = default_chunk_size
        self._default_chunk_overlap = default_chunk_overlap

    # ── Ingest ────────────────────────────────────────────────────────────────

    async def ingest(
        self,
        content: str | list[str],
        *,
        collection: str = "default",
        chunker: str = "text",
        metadata: dict[str, Any] | None = None,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> int:
        """Chunk, embed, and store content.

        Args:
            content: Text (or list of texts) to ingest.
            collection: Namespace in the vector store.
            chunker: Chunking strategy name (``"text"`` or ``"sentence"``).
            metadata: Base metadata applied to every chunk.
            chunk_size: Override default chunk size.
            chunk_overlap: Override default chunk overlap.

        Returns:
            Number of document chunks stored.
        """
        # Normalise to list
        texts = [content] if isinstance(content, str) else content

        # Build chunker
        chunker_kwargs: dict[str, Any] = {}
        if chunker == "text":
            chunker_kwargs["chunk_size"] = chunk_size or self._default_chunk_size
            chunker_kwargs["overlap"] = chunk_overlap or self._default_chunk_overlap
        elif chunker == "sentence":
            if chunk_size:
                chunker_kwargs["max_chunk_size"] = chunk_size

        chunker_instance = get_chunker(chunker, **chunker_kwargs)

        # Chunk all texts
        all_docs: list[Document] = []
        for text in texts:
            docs = chunker_instance.chunk(text, metadata=metadata)
            all_docs.extend(docs)

        if not all_docs:
            return 0

        # Embed all chunks in a single batch
        chunk_texts = [doc.to_text() for doc in all_docs]
        result = await self._embedding.embed(chunk_texts)

        # Populate embedding on documents
        docs_with_embeddings = [
            dataclasses.replace(doc, embedding=emb)
            for doc, emb in zip(all_docs, result.embeddings)
        ]

        # Store
        await self._store.add(docs_with_embeddings, collection=collection)

        logger.info(
            "Ingested %d chunks into collection '%s' (%d tokens used)",
            len(all_docs),
            collection,
            result.usage_tokens,
        )
        return len(all_docs)

    async def ingest_documents(
        self,
        documents: list[Document],
        *,
        collection: str = "default",
    ) -> int:
        """Embed and store pre-chunked documents.

        Use this when documents are already prepared (e.g. by a loader).
        """
        if not documents:
            return 0

        chunk_texts = [doc.to_text() for doc in documents]
        result = await self._embedding.embed(chunk_texts)
        docs_with_embeddings = [
            dataclasses.replace(doc, embedding=emb)
            for doc, emb in zip(documents, result.embeddings)
        ]
        await self._store.add(docs_with_embeddings, collection=collection)
        return len(documents)

    # ── Query ─────────────────────────────────────────────────────────────────

    async def query(
        self,
        question: str,
        *,
        collection: str = "default",
        limit: int = 5,
        filter: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """Embed the question and search the vector store.

        Returns the top-``limit`` most similar documents.
        """
        query_vec = await self._embedding.embed_single(question)
        return await self._store.search(
            query_vec,
            collection=collection,
            limit=limit,
            filter=filter,
        )

    async def query_with_context(
        self,
        question: str,
        *,
        collection: str = "default",
        model_client: LLMClient,
        limit: int = 5,
        system: str | None = None,
        filter: dict[str, Any] | None = None,
    ) -> str:
        """Full RAG: retrieve context, build prompt, generate answer.

        Args:
            question: User question.
            collection: Vector store collection to search.
            model_client: LLM client to use for generation.
            limit: Number of context chunks to retrieve.
            system: Optional system prompt override.
            filter: Optional metadata filter.

        Returns:
            The generated answer string.
        """
        from agent_substrate.kernel import ChatMessage, TextBlock

        results = await self.query(
            question,
            collection=collection,
            limit=limit,
            filter=filter,
        )

        # Build context block
        context_parts: list[str] = []
        for i, r in enumerate(results, 1):
            context_parts.append(f"[{i}] {r.to_text()}")
        context_block = "\n\n".join(context_parts)

        system_prompt = system or (
            "You are a helpful assistant. Answer the user's question using "
            "ONLY the provided context. If the context doesn't contain the "
            "answer, say so."
        )

        messages = [
            ChatMessage(role="user", content=[TextBlock(text=question)]),
        ]

        from agent_substrate.kernel.llm import GenerationOptions

        response = await model_client.generate(
            messages,
            options=GenerationOptions(
                system_instructions=f"{system_prompt}\n\nContext:\n{context_block}"
            ),
        )

        # Extract text from response blocks
        text_parts = [b.text for b in response.content if isinstance(b, TextBlock)]
        return "".join(text_parts)
