"""Wire schemas for the Docling service.

Re-exports the canonical response shape from the capabilities layer (single
source of truth — mirrors the code_interpreter service's schemas.py, which
does the same for its own request/response types) and adds the
service-local request shape.
"""

from __future__ import annotations

from pydantic import BaseModel

from substrate.capabilities.knowledge.docling_client import DoclingExtractResponse

__all__ = ["ExtractRequest", "DoclingExtractResponse", "HealthResponse"]


class ExtractRequest(BaseModel):
    content_base64: str
    filename: str
    content_type: str


class HealthResponse(BaseModel):
    status: str
    pod_name: str
    uptime_seconds: float
