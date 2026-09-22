"""PDF document loader — delegates extraction to a kernel ``DocumentExtractor``.

Produces one ``Document`` per page. Defaults to ``LocalDocumentExtractor``
(``agents.document`` — pdfplumber/pypdf text layer + a bare-minimum Tesseract
OCR fallback for scanned pages); pass any other ``DocumentExtractor`` (e.g.
``ServiceBackedDocumentExtractor`` for the PaddleOCR-backed service) to swap
in stronger extraction without changing this loader.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any, Union

from substrate.agents.document import DocumentExtractor, LocalDocumentExtractor
from substrate.integrations.knowledge.loaders.base import BaseDocumentLoader
from substrate.kernel.core.content import TextBlock
from substrate.kernel.storage.vector import Document
from substrate.logger import setup_logging

logger = setup_logging()


def _page_id(source: str, page_number: int, text: str) -> str:
    """Deterministic, content-addressed page ID.

    The same (source, page, text) always yields the same UUID, so re-ingesting
    an unchanged document is idempotent under ``ON CONFLICT (id) DO NOTHING``.
    Including the text hash means an edited page is treated as a new row.
    """
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    key = f"{source}|{page_number}|{digest}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


class PDFLoader(BaseDocumentLoader):
    """Load PDF files — one ``Document`` per page.

    Delegates all extraction (and any fallback behavior) to a single
    injected ``DocumentExtractor`` — defaults to ``LocalDocumentExtractor``.
    Pass ``extractor=ServiceBackedDocumentExtractor(...)`` for the
    PaddleOCR-backed service instead; that adapter already falls back to
    local raw-text extraction internally on service failure, so this loader
    doesn't need its own fallback chain.
    """

    def __init__(
        self,
        extract_tables: bool = True,
        *,
        extractor: DocumentExtractor | None = None,
    ) -> None:
        self.extract_tables = extract_tables
        self._extractor: DocumentExtractor = extractor or LocalDocumentExtractor(
            extract_tables=extract_tables
        )

    async def load(
        self,
        source: Union[str, Path, bytes],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> list[Document]:
        metadata = metadata or {}
        data = source if isinstance(source, bytes) else Path(source).read_bytes()
        name = str(
            metadata.get("filename")
            or metadata.get("source")
            or (source if isinstance(source, (str, Path)) else "document.pdf")
        )
        if not isinstance(source, bytes):
            metadata.setdefault("source", str(source))

        result = await self._extractor.extract(data, name)
        if not result.success:
            logger.info("Extraction failed for %r: %s", name, result.error)
            return []

        source_str = str(metadata.get("source", ""))
        return [
            Document(
                content=[TextBlock(text=page.text)],
                metadata={
                    **metadata,
                    "engine": result.engine,
                    "page_number": page.page_number,
                    "total_pages": len(result.pages),
                },
                id=_page_id(source_str, page.page_number, page.text),
            )
            for page in result.pages
            if page.text.strip()
        ]
