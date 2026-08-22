"""substrate.runtimes.document_intelligence — document extraction, its
security scan, and the lightweight client for calling it. See
document_intelligence/service/ for the FastAPI microservice itself (heavy
PaddleOCR runtime, opt-in `document-intelligence`/`document-intelligence-gpu`
extras); client.py and security_scan.py have no heavy dependencies and are
always importable from the base install.
"""

from __future__ import annotations

from substrate.runtimes.document_intelligence.client import (
    ExtractedImage,
    ExtractedPageText,
    ExtractionClient,
    ExtractResponse,
)

__all__ = [
    "ExtractionClient",
    "ExtractResponse",
    "ExtractedImage",
    "ExtractedPageText",
]
