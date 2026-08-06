"""Pluggable RAG backends behind one ``RagBackend`` contract.

Mirrors the ``SandboxRuntime`` pattern
(``capabilities/tools/code_interpreter/code_interpreter/runtimes/``): one
coarse Protocol, a handful of concrete backends, and a ``build_rag_backend``
factory. Construct-and-pass, exactly like an LLM client::

    from substrate.capabilities.knowledge.backends import build_rag_backend

    rag = build_rag_backend("pinecone", api_key="...", assistant_name="docs")
    # or: rag = build_rag_backend("local", embedding_client=..., vector_store=...)

| Backend | What it wraps | Needs |
|---|---|---|
| ``LocalRagBackend`` | Existing `RAGPipeline` + `PgVectorStore` + loaders + `LLMReranker` | Postgres/pgvector, an embedding client |
| ``PineconeRagBackend`` | Pinecone Assistant (parse+chunk+embed+store+retrieve, opaque) | `PINECONE_API_KEY` |
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import IngestResult, RagBackend, RagBackendUnavailableError
from .local import LocalRagBackend

if TYPE_CHECKING:
    from .pinecone import PineconeRagBackend


def __getattr__(name: str) -> Any:
    # Lazy: the pinecone SDK is an optional dependency (the `pinecone` extra),
    # not installed by default — importing it eagerly would break every
    # deployment that only uses the local backend.
    if name == "PineconeRagBackend":
        from .pinecone import PineconeRagBackend

        return PineconeRagBackend
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


from .factory import build_rag_backend  # noqa: E402 - after __getattr__ definition

__all__ = [
    "IngestResult",
    "LocalRagBackend",
    "PineconeRagBackend",
    "RagBackend",
    "RagBackendUnavailableError",
    "build_rag_backend",
]
