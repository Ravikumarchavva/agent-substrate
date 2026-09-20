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
    """Three valid shapes: ``text`` alone, ``image_base64`` alone (the
    single-image case), or ``text`` + ``images_base64`` together — the
    mixed-multimodal case, embedded as one vector for the whole combined
    input via a single prompt with the images' dynamic media_marker
    interleaved server-side. ``image_base64`` is mutually exclusive with
    both ``text`` and ``images_base64`` — it's the single-image-only
    shortcut; use ``images_base64`` (even with one entry) alongside
    ``text`` for anything mixed."""

    image_base64: str | None = None
    text: str | None = None
    images_base64: list[str] | None = None


class RerankRequest(BaseModel):
    query: str
    passages: list[str]
