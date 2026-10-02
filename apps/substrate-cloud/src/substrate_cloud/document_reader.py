"""The platform's one way to read an uploaded document: ``substrate.documents.Reader``, pointed at the document-intelligence service when
``DOCUMENT_INTELLIGENCE_SERVICE_URL`` is set (layout, tables, chart crops, PaddleOCR), else the library's built-in reader (PDFium, native
Office/HTML, Tesseract or RapidOCR for scans). Same result either way, and a service that is down falls back to the built-in reader."""

from __future__ import annotations

from typing import Any

from substrate.documents import Reader

from substrate_cloud.shared.settings import settings


def document_reader(cfg: Any = None) -> Reader:
    cfg = cfg or settings
    return Reader(
        cfg.DOCUMENT_INTELLIGENCE_SERVICE_URL or None,
        api_key=cfg.DOCUMENT_INTELLIGENCE_AUTH_TOKEN,
        timeout=float(cfg.DOCUMENT_INTELLIGENCE_TIMEOUT_S),
    )


__all__ = ["document_reader"]
