"""substrate.kernel.document — local filesystem document store.

The DocumentExtractor contract lives in the kernel; extraction backends (the
local PDF/OCR extractor, the PaddleOCR service) are adapters in ``integrations``.
"""

from __future__ import annotations

from substrate.kernel.document.local_document_store import LocalFilesystemDocumentStore

__all__ = ["LocalFilesystemDocumentStore"]
