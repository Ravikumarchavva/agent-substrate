"""Wire schemas for the extraction service.

Re-exports the canonical response shapes from the capabilities layer (single
source of truth — mirrors the code_interpreter service's schemas.py, which
does the same for its own request/response types) and adds the
service-local request shapes.
"""

from __future__ import annotations

from pydantic import BaseModel

from substrate.capabilities.knowledge.extraction_client import (
    ExtractedImage,
    ExtractedPageText,
    ExtractResponse,
)

__all__ = [
    "ExtractRequest",
    "ExtractedImage",
    "ExtractedPageText",
    "ExtractResponse",
    "EmbedRequest",
    "EmbedResponse",
    "RerankRequest",
    "RerankResponse",
    "HealthResponse",
]


class ExtractRequest(BaseModel):
    content_base64: str
    filename: str
    content_type: str


class EmbedRequest(BaseModel):
    """Exactly one of ``image_base64``/``text`` must be set — embedding an
    image (a chart crop) and embedding a text query use the same model's
    two towers, but never both inputs in one call."""

    image_base64: str | None = None
    text: str | None = None


class EmbedResponse(BaseModel):
    embedding: list[float]


class RerankRequest(BaseModel):
    query: str
    passages: list[str]


class RerankResponse(BaseModel):
    scores: list[float]


class HealthResponse(BaseModel):
    status: str
    pod_name: str
    uptime_seconds: float
