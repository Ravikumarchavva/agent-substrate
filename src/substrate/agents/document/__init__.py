"""substrate.agents.document — kernel DocumentExtractor contract and its
one L1 default implementation.

``LocalDocumentExtractor`` is the L1 default — the same "one implementation
needing the least infrastructure the Protocol can possibly need" rule every
other storage Protocol already follows. Stronger adapters (e.g. the
PaddleOCR-backed service) live in ``integrations``/``runtimes`` for L2.
"""

from __future__ import annotations

from substrate.kernel.document import (
    DocumentChunker,
    DocumentExtractor,
    DocumentStore,
    ExtractedImage,
    ExtractedImageLabel,
    ExtractedPage,
    ExtractionResult,
)
from substrate.agents.document.local_extractor import LocalDocumentExtractor

__all__ = [
    "DocumentExtractor",
    "DocumentChunker",
    "DocumentStore",
    "ExtractedImage",
    "ExtractedImageLabel",
    "ExtractedPage",
    "ExtractionResult",
    "LocalDocumentExtractor",
]
