"""substrate.doc_handler — document extraction, its security scan, and the
lightweight client for calling it. See doc_handler/service/ for the
FastAPI microservice itself (heavy PaddleOCR runtime, opt-in `doc-handler`/
`doc-handler-gpu` extras); client.py and security_scan.py have no heavy
dependencies and are always importable from the base install.
"""

from __future__ import annotations

from substrate.doc_handler.client import (
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
