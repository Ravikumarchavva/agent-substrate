"""Wire schemas for the embedding-reranker service.

Re-exports the canonical response shapes from embedding_reranker/client.py
(single source of truth — mirrors document_intelligence's own
service/schemas.py, which does the same for its own request/response types)
and adds the service-local request shapes.
"""

from __future__ import annotations

from pydantic import BaseModel

from substrate.runtimes.embedding_reranker.client import (
    EmbedResponse,
    HealthResponse,
    RerankResponse,
)

__all__ = [
    "EmbedRequest",
    "EmbedResponse",
    "RerankRequest",
    "RerankResponse",
    "HealthResponse",
]


class EmbedRequest(BaseModel):
    """Exactly one of ``image_base64``/``text`` must be set — embedding an
    image (a chart crop) and embedding a text query use the same model's
    two towers, but never both inputs in one call."""

    image_base64: str | None = None
    text: str | None = None


class RerankRequest(BaseModel):
    query: str
    passages: list[str]
