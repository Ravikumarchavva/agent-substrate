"""substrate.integrations.vector — the PostgreSQL ``VectorStore`` (pgvector).

The folder store's vectors are ``Store.vectors``; this is the backend for deployments that keep chunks in PostgreSQL.
"""

from __future__ import annotations

from substrate.integrations.vector.pgvector_store import PgVectorStore

__all__ = ["PgVectorStore"]
