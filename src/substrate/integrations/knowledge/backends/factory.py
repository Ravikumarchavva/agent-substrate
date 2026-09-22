"""build_rag_backend — turn a name + kwargs into a concrete RagBackend.

One explicit switch, mirroring
``code_interpreter/.../runtimes/factory.py::build_runtime``. Works two ways:

* **Library use** — call directly with explicit kwargs, exactly like
  ``LLMFactory("gpt-4o", api_key=...).build()``::

      rag = build_rag_backend("local", embedding_client=..., vector_store=...)

* **Server default** — ``serving_factory.py`` calls this with ``cfg.RAG_BACKEND``
  and the pieces it already constructs (embedding client, vector store, ...).

``"local"`` (``LocalRagBackend``, backed by ``PgVectorStore``) is the only
backend today. A managed-service backend (Pinecone Assistant) existed
briefly but was removed as dead weight — never the standard path, and this
project's own per-user session-document index already uses LanceDB
(``capabilities/vector/lancedb_store.py``) where a second, self-hosted
backend was actually needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import RagBackend, RagBackendUnavailableError
from .local import LocalRagBackend

if TYPE_CHECKING:
    from substrate.kernel.llm import EmbeddingClient, LLMClient
    from substrate.kernel.storage.vector import VectorStore


def build_rag_backend(kind: str, **kwargs: Any) -> RagBackend:
    """Construct the backend named by *kind*.

    ``kind="local"`` kwargs: ``embedding_client`` (required), ``vector_store``
    (required), ``model_client`` (optional — needed for ``query_with_context``,
    and as the ``LLMReranker`` fallback when ``rerank=True`` but no
    extraction service is configured), ``rerank`` (bool, default ``False``),
    ``image_store`` (optional — a second ``VectorStore`` for chart/table
    images, dimensionality matching the embedding-reranker service's
    embedding model), ``extraction_service_url``/``extraction_auth_token``/
    ``extraction_timeout_s`` (optional — layout-aware parsing, chart-image
    extraction; falls back to pypdf/pdfplumber text-only parsing without
    one), ``embedding_reranker_service_url``/
    ``embedding_reranker_auth_token``/``embedding_reranker_timeout_s``
    (optional — multimodal embedding and a local cross-encoder reranker;
    falls back to an ``LLMReranker`` when ``rerank=True`` but not configured).
    ``dense_k``/``lexical_k``/``fused_k``/``rerank_top_n`` (optional —
    hybrid-retrieval budgets, forwarded to ``LocalRagBackend``; see
    config.py's ``RAG_DENSE_K`` etc. for the defaults these mirror).
    ``chunk_size``/``chunk_overlap`` (optional — forwarded to the
    underlying ``RAGPipeline`` as its default chunk size/overlap; see
    config.py's ``RAG_CHUNK_SIZE``/``RAG_CHUNK_OVERLAP``). When either is
    left unset (``None``), it's derived from ``embedding_model`` (optional
    — the raw model string, e.g. ``cfg.EMBEDDING_MODEL``) via
    ``chunking.py::recommend_chunk_params`` instead of one flat default.

    Raises ``RagBackendUnavailableError`` for an unknown name or missing
    prerequisites — fail loudly at construction, not at the first real call.
    """
    name = kind.strip().lower()

    if name == "local":
        from substrate.capabilities.knowledge.pipeline import RAGPipeline

        embedding_client: EmbeddingClient | None = kwargs.get("embedding_client")
        vector_store: VectorStore | None = kwargs.get("vector_store")
        if embedding_client is None or vector_store is None:
            raise RagBackendUnavailableError(
                "build_rag_backend('local', ...) requires embedding_client "
                "and vector_store."
            )
        model_client: LLMClient | None = kwargs.get("model_client")
        image_store: VectorStore | None = kwargs.get("image_store")
        extraction_service_url = kwargs.get("extraction_service_url", "")
        extraction_auth_token = kwargs.get("extraction_auth_token", "")
        extraction_timeout_s = kwargs.get("extraction_timeout_s", 90)

        embedding_reranker_service_url = kwargs.get(
            "embedding_reranker_service_url", ""
        )
        embedding_reranker_auth_token = kwargs.get("embedding_reranker_auth_token", "")
        embedding_reranker_timeout_s = kwargs.get("embedding_reranker_timeout_s", 30)

        embedding_reranker_client = None
        if embedding_reranker_service_url:
            from substrate.runtimes.embedding_reranker.client import (
                EmbeddingRerankerClient,
            )

            embedding_reranker_client = EmbeddingRerankerClient(
                base_url=embedding_reranker_service_url,
                auth_token=embedding_reranker_auth_token,
                timeout_s=embedding_reranker_timeout_s,
            )

        reranker = None
        if kwargs.get("rerank"):
            if embedding_reranker_client is not None:
                # Local cross-encoder — no LLM tokens/latency spent on
                # reranking. Preferred whenever the embedding-reranker
                # service (and therefore its reranker model) is configured.
                from substrate.capabilities.knowledge.reranker import (
                    CrossEncoderReranker,
                )

                reranker = CrossEncoderReranker(embedding_reranker_client)
            elif model_client is not None:
                from substrate.capabilities.knowledge.reranker import LLMReranker

                reranker = LLMReranker(model_client)

        chunk_size = kwargs.get("chunk_size")
        chunk_overlap = kwargs.get("chunk_overlap")
        if chunk_size is None or chunk_overlap is None:
            # None means "let the configured embedding model decide" --
            # see chunking.py::recommend_chunk_params's own docstring for
            # why this beats one flat default for every provider. An
            # explicit chunk_size/chunk_overlap always wins per-field
            # (same explicit-wins precedence used throughout this codebase).
            from substrate.capabilities.knowledge.chunking import (
                recommend_chunk_params,
            )

            recommended_size, recommended_overlap = recommend_chunk_params(
                kwargs.get("embedding_model") or ""
            )
            chunk_size = chunk_size if chunk_size is not None else recommended_size
            chunk_overlap = (
                chunk_overlap if chunk_overlap is not None else recommended_overlap
            )

        return LocalRagBackend(
            RAGPipeline(
                embedding_client,
                vector_store,
                default_chunk_size=chunk_size,
                default_chunk_overlap=chunk_overlap,
            ),
            vector_store=vector_store,
            image_store=image_store,
            extraction_service_url=extraction_service_url,
            extraction_auth_token=extraction_auth_token,
            extraction_timeout_s=extraction_timeout_s,
            embedding_reranker_service_url=embedding_reranker_service_url,
            embedding_reranker_auth_token=embedding_reranker_auth_token,
            embedding_reranker_timeout_s=embedding_reranker_timeout_s,
            embedding_reranker_client=embedding_reranker_client,
            reranker=reranker,
            model_client=model_client,
            file_store=kwargs.get("file_store"),
            dense_k=kwargs.get("dense_k", 50),
            lexical_k=kwargs.get("lexical_k", 50),
            fused_k=kwargs.get("fused_k", 50),
            rerank_top_n=kwargs.get("rerank_top_n", 10),
        )

    raise RagBackendUnavailableError(f"Unknown RAG_BACKEND {kind!r}. Valid: local.")


__all__ = ["build_rag_backend"]
