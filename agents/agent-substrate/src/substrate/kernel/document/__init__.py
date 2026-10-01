"""substrate.agents.document — kernel DocumentExtractor contract and its
one L1 default implementation.

``LocalDocumentExtractor`` and ``LocalFilesystemDocumentStore`` are the L1
defaults — the same "one implementation needing the least infrastructure the
Protocol can possibly need" rule every other storage Protocol already follows.
(``DocumentChunker``'s default lives with the RAG stack, in
``integrations/knowledge/chunking.py``.) Stronger adapters (e.g. the
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
from substrate.agents.document.local_document_store import LocalFilesystemDocumentStore
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
    "LocalFilesystemDocumentStore",
]
