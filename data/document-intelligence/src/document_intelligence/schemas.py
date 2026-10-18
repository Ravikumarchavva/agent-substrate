"""Wire schemas of the document-intelligence service.

The answer to ``POST /v1/extract`` is the library's own ``ExtractionResult`` (``substrate.documents``), as JSON: pages with
``<!-- page N -->`` markers, ``needs_ocr``, figures as base64 images. Only the request and the health report are the service's own.
"""

from __future__ import annotations

from pydantic import BaseModel

from substrate.documents import Strategy

__all__ = ["ExtractRequest", "HealthResponse"]


class ExtractRequest(BaseModel):
    content_base64: str
    filename: str
    content_type: str = ""
    strategy: Strategy = "auto"


class HealthResponse(BaseModel):
    status: str
    pod_name: str
    uptime_seconds: float
    # Which engine this pod resolved to and serves PDFs and images with: "native" | "paddleocr-vl" | "ppstructurev3".
    engine: str = ""
    # The mode ServiceConfig.mode requested vs. what autoconfig.resolve_runtime resolved to on this hardware.
    requested_mode: str = ""
    resolved_mode: str = ""
    # Non-None only when the requested mode could not be satisfied on this hardware and autoconfig degraded to another one.
    # A silent capability downgrade must never actually be silent.
    degraded_from: str | None = None
    worker_count: int = 0
