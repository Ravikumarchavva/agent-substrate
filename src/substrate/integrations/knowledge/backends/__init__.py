"""Pluggable RAG backends behind one ``RagBackend`` contract.

Mirrors the ``SandboxRuntime`` pattern
(``capabilities/tools/code_interpreter/code_interpreter/runtimes/``): one
coarse Protocol, a handful of concrete backends, and a ``build_rag_backend``
factory. Construct-and-pass, exactly like an LLM client::

    from substrate.integrations.knowledge.backends import build_rag_backend

    rag = build_rag_backend("local", embedding_client=..., vector_store=...)

| Backend | What it wraps | Needs |
|---|---|---|
| ``LocalRagBackend`` | Existing `RAGPipeline` + `PgVectorStore` + loaders + `LLMReranker` | Postgres/pgvector, an embedding client |

``LocalRagBackend`` is the only backend now — a managed-service backend
(``PineconeRagBackend``) existed briefly but was removed: real dead weight,
never the standard path, and this project's per-user session-document
index already uses LanceDB (``capabilities/vector/lancedb_store.py``) as
its own embedded/self-hosted vector store where a second backend was
actually needed.
"""

from __future__ import annotations

from .base import IngestResult, RagBackend, RagBackendUnavailableError
from .factory import build_rag_backend
from .local import LocalRagBackend

__all__ = [
    "IngestResult",
    "LocalRagBackend",
    "RagBackend",
    "RagBackendUnavailableError",
    "build_rag_backend",
]
