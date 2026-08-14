"""build_rag_backend — turn a name + kwargs into a concrete RagBackend.

One explicit switch, mirroring
``code_interpreter/.../runtimes/factory.py::build_runtime``. Works two ways:

* **Library use** — call directly with explicit kwargs, exactly like
  ``LLMFactory("gpt-4o", api_key=...).build()``::

      rag = build_rag_backend("pinecone", api_key="...", assistant_name="docs")

* **Server default** — ``serving_factory.py`` calls this with ``cfg.RAG_BACKEND``
  and the pieces it already constructs (embedding client, vector store, ...),
  so ``RAG_BACKEND=pinecone`` switches the whole server's RAG (HTTP routes and
  the agent tool alike) in one place.
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
    images, dimensionality matching the extraction service's embedding
    model), ``extraction_service_url``/``extraction_auth_token``/
    ``extraction_timeout_s`` (optional — layout-aware parsing, chart-image
    extraction, multimodal embedding, and a local cross-encoder reranker;
    falls back to pypdf/pdfplumber text-only parsing without one).
    ``dense_k``/``lexical_k``/``fused_k``/``rerank_top_n`` (optional —
    hybrid-retrieval budgets, forwarded to ``LocalRagBackend``; see
    config.py's ``RAG_DENSE_K`` etc. for the defaults these mirror).

    ``kind="pinecone"`` kwargs: ``api_key`` (falls back to
    ``PINECONE_API_KEY`` env var), ``assistant_name`` (required).

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

        extraction_client = None
        if extraction_service_url:
            from substrate.capabilities.knowledge.extraction_client import (
                ExtractionClient,
            )

            extraction_client = ExtractionClient(
                base_url=extraction_service_url,
                auth_token=extraction_auth_token,
                timeout_s=extraction_timeout_s,
            )

        reranker = None
        if kwargs.get("rerank"):
            if extraction_client is not None:
                # Local cross-encoder — no LLM tokens/latency spent on
                # reranking. Preferred whenever the extraction service (and
                # therefore its reranker model) is configured.
                from substrate.capabilities.knowledge.reranker import (
                    CrossEncoderReranker,
                )

                reranker = CrossEncoderReranker(extraction_client)
            elif model_client is not None:
                from substrate.capabilities.knowledge.reranker import LLMReranker

                reranker = LLMReranker(model_client)

        return LocalRagBackend(
            RAGPipeline(embedding_client, vector_store),
            vector_store=vector_store,
            image_store=image_store,
            extraction_service_url=extraction_service_url,
            extraction_auth_token=extraction_auth_token,
            extraction_timeout_s=extraction_timeout_s,
            extraction_client=extraction_client,
            reranker=reranker,
            model_client=model_client,
            file_store=kwargs.get("file_store"),
            dense_k=kwargs.get("dense_k", 50),
            lexical_k=kwargs.get("lexical_k", 50),
            fused_k=kwargs.get("fused_k", 50),
            rerank_top_n=kwargs.get("rerank_top_n", 10),
        )

    if name == "pinecone":
        import os

        from .pinecone import PineconeRagBackend

        api_key = kwargs.get("api_key") or os.environ.get("PINECONE_API_KEY", "")
        assistant_name = kwargs.get("assistant_name", "")
        if not assistant_name:
            raise RagBackendUnavailableError(
                "build_rag_backend('pinecone', ...) requires assistant_name."
            )
        return PineconeRagBackend(api_key=api_key, assistant_name=assistant_name)

    raise RagBackendUnavailableError(
        f"Unknown RAG_BACKEND {kind!r}. Valid: local, pinecone."
    )


__all__ = ["build_rag_backend"]
