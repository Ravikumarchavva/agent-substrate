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
    "ExtractBatchRequest",
    "ExtractedImage",
    "ExtractedPageText",
    "ExtractResponse",
    "HealthResponse",
]


class ExtractRequest(BaseModel):
    content_base64: str
    filename: str
    content_type: str


class ExtractBatchRequest(BaseModel):
    """Same shape as ``ExtractRequest``, batched — see ``/v1/extract-batch``.
    Real reason this exists as a separate endpoint rather than accepting a
    ``list[ExtractRequest]`` there: single-file callers (e.g. chat
    attachments) shouldn't have to build a one-element list for the common
    case."""

    items: list[ExtractRequest]


class HealthResponse(BaseModel):
    status: str
    pod_name: str
    uptime_seconds: float
    # Which concrete engine this pod actually resolved to and is serving
    # requests with -- "raw_text" | "paddleocr-vl" | "ppstructurev3". Lets
    # a caller/dashboard see what's really running without guessing from
    # the requested mode alone.
    engine: str = ""
    # The mode ServiceConfig.mode requested vs. what autoconfig.py's
    # resolve_runtime actually resolved to on this hardware -- "auto" |
    # "raw_text" | "vl_cpu" | "vl_gpu" | "ocr_classic".
    requested_mode: str = ""
    resolved_mode: str = ""
    # Non-None only when the requested mode couldn't be satisfied on this
    # hardware and autoconfig degraded to another one (e.g. vl_gpu
    # requested, no eligible GPU present -> vl_cpu served). A silent
    # capability downgrade must never actually be silent.
    degraded_from: str | None = None
    worker_count: int = 0
