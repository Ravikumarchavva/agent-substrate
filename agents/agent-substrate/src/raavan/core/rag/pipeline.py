"""RAG Pipeline — end-to-end ingest + query orchestrator.

Wires together an embedding client, vector store, and chunker into a
single high-level interface.

Usage::

    from raavan.core.rag.pipeline import RAGPipeline

    pipeline = RAGPipeline(
        embedding_client=embed_client,
        vector_store=vector_store,
    )
    await pipeline.ingest("Long doc ...", collection="kb")
    results = await pipeline.query("What is X?", collection="kb")
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

from raavan.core.rag.chunking import get_chunker
from raavan.core.rag.vector_store import BaseVectorStore, Document, SearchResult

if TYPE_CHECKING:
    from raavan.core.llm.base_client import BaseModelClient
    from raavan.core.llm.base_embedding_client import BaseEmbeddingClient

logger = logging.getLogger(__name__)


class RAGPipeline:
    """End-to-end Retrieval-Augmented Generation pipeline.

    Handles: chunk → embed → store (ingest) and embed → search (query).
    Optionally generates an answer using a model client (query_with_context).
    """

    def __init__(
        self,
        embedding_client: BaseEmbeddingClient,
        vector_store: BaseVectorStore,
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
        metadata: Optional[dict[str, Any]] = None,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
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
        chunk_texts = [doc.text for doc in all_docs]
        result = await self._embedding.embed(chunk_texts)

        # Store
        await self._store.add(all_docs, result.embeddings, collection=collection)

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

        chunk_texts = [doc.text for doc in documents]
        result = await self._embedding.embed(chunk_texts)
        await self._store.add(documents, result.embeddings, collection=collection)
        return len(documents)

    # ── Query ─────────────────────────────────────────────────────────────────

    async def query(
        self,
        question: str,
        *,
        collection: str = "default",
        limit: int = 5,
        filter: Optional[dict[str, Any]] = None,
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
        model_client: BaseModelClient,
        limit: int = 5,
        system: Optional[str] = None,
        filter: Optional[dict[str, Any]] = None,
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
        from raavan.core.messages.client_messages import SystemMessage, UserMessage

        results = await self.query(
            question,
            collection=collection,
            limit=limit,
            filter=filter,
        )

        # Build context block
        context_parts: list[str] = []
        for i, r in enumerate(results, 1):
            context_parts.append(f"[{i}] {r.text}")
        context_block = "\n\n".join(context_parts)

        system_prompt = system or (
            "You are a helpful assistant. Answer the user's question using "
            "ONLY the provided context. If the context doesn't contain the "
            "answer, say so."
        )

        messages = [
            SystemMessage(content=f"{system_prompt}\n\nContext:\n{context_block}"),
            UserMessage(role="user", content=[question]),
        ]

        response = await model_client.generate_text(messages)

        # Extract text from AssistantMessage
        if response.content:
            return "".join(part for part in response.content if isinstance(part, str))
        return ""
