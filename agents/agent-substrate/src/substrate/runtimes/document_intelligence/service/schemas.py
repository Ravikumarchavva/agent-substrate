"""Wire schemas for the document-intelligence service.

Re-exports the canonical response shapes from document_intelligence/client.py
(single source of truth — mirrors the code_interpreter service's schemas.py,
which does the same for its own request/response types) and adds the
service-local request shapes.
"""

from __future__ import annotations

from pydantic import BaseModel

from substrate.runtimes.document_intelligence.client import (
    ExtractedImage,
    ExtractedPageText,
    ExtractResponse,
)

__all__ = [
    "ExtractRequest",
    "ExtractedImage",
    "ExtractedPageText",
    "ExtractResponse",
    "HealthResponse",
]


class ExtractRequest(BaseModel):
    content_base64: str
    filename: str
    content_type: str


class HealthResponse(BaseModel):
    status: str
    pod_name: str
    uptime_seconds: float
