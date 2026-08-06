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
    (required), ``model_client`` (optional — needed for ``query_with_context``
    and for the reranker), ``rerank`` (bool, default ``False``),
    ``docling_service_url``/``docling_auth_token``/``docling_timeout_s``
    (optional — parsing falls back to pypdf/pdfplumber without one).

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
        reranker = None
        if kwargs.get("rerank") and model_client is not None:
            from substrate.capabilities.knowledge.reranker import LLMReranker

            reranker = LLMReranker(model_client)
        return LocalRagBackend(
            RAGPipeline(embedding_client, vector_store),
            vector_store=vector_store,
            docling_service_url=kwargs.get("docling_service_url", ""),
            docling_auth_token=kwargs.get("docling_auth_token", ""),
            docling_timeout_s=kwargs.get("docling_timeout_s", 90),
            reranker=reranker,
            model_client=model_client,
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
