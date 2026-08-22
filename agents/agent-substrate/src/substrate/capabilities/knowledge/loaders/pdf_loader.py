"""PDF document loader using pdfplumber.

Produces one ``Document`` per page with layout-aware text extraction and
optional table extraction.  Falls back to pypdf if pdfplumber fails.

Requires the optional dependency group: ``uv sync --group optional``
"""

from __future__ import annotations
from substrate.logger import setup_logging

import hashlib
import io
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

from substrate.capabilities.knowledge.loaders.base import BaseDocumentLoader
from substrate.kernel.core.content import TextBlock
from substrate.kernel.storage.vector import Document

if TYPE_CHECKING:
    from substrate.runtimes.document_intelligence.client import ExtractionClient

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

    Tries the document-intelligence service first when *extraction_client*
    is given (layout-aware: chart/table detection, OCR) — same behavior as
    ``LocalRagBackend._load_via_extraction_service``, pushed down into this
    loader for callers that instantiate it bare. Falls back to
    ``pdfplumber`` for layout-aware text + table extraction on any service
    failure or when no client is configured, and further to ``pypdf`` if
    pdfplumber is not installed.
    """

    def __init__(
        self,
        extract_tables: bool = True,
        *,
        extraction_client: "ExtractionClient | None" = None,
    ) -> None:
        self.extract_tables = extract_tables
        self._extraction_client = extraction_client

    async def load(
        self,
        source: Union[str, Path, bytes],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> list[Document]:
        metadata = metadata or {}

        if self._extraction_client is not None:
            docs = await self._load_via_extraction_service(source, metadata)
            if docs is not None:
                return docs
            logger.info(
                "Extraction service failed/unavailable, falling back to local parse"
            )

        docs: list[Document] = []
        try:
            docs = await self._load_with_pdfplumber(source, metadata)
        except ImportError:
            logger.info("pdfplumber not available, falling back to pypdf")
            docs = await self._load_with_pypdf(source, metadata)

        return docs

    async def _load_via_extraction_service(
        self,
        source: Union[str, Path, bytes],
        metadata: dict[str, Any],
    ) -> list[Document] | None:
        assert self._extraction_client is not None
        data = source if isinstance(source, bytes) else Path(source).read_bytes()
        name = str(
            metadata.get("filename")
            or metadata.get("source")
            or (source if isinstance(source, (str, Path)) else "document.pdf")
        )
        result = await self._extraction_client.extract(data, name, "application/pdf")
        if not result.success:
            return None

        source_str = str(metadata.get("source", ""))
        docs = [
            Document(
                content=[TextBlock(text=page.text)],
                metadata={
                    **metadata,
                    "engine": result.engine,
                    "page_number": page.page_number,
                    "total_pages": result.page_count,
                },
                id=_page_id(source_str, page.page_number, page.text),
            )
            for page in result.pages
            if page.text.strip()
        ]
        return docs or None

    async def _load_with_pdfplumber(
        self,
        source: Union[str, Path, bytes],
        metadata: dict[str, Any],
    ) -> list[Document]:
        import pdfplumber  # type: ignore[import-unresolved]

        if isinstance(source, bytes):
            pdf = pdfplumber.open(io.BytesIO(source))
        else:
            path = Path(source)
            metadata.setdefault("source", str(path))
            pdf = pdfplumber.open(path)

        docs: list[Document] = []
        with pdf:
            for i, page in enumerate(pdf.pages):
                parts: list[str] = []

                # Extract text
                text = page.extract_text() or ""
                if text.strip():
                    parts.append(text)

                # Extract tables
                if self.extract_tables:
                    tables = page.extract_tables()
                    for table in tables:
                        rows: list[str] = []
                        for row in table:
                            cells = [str(c) if c else "" for c in row]
                            rows.append(" | ".join(cells))
                        if rows:
                            parts.append("\n".join(rows))

                page_text = "\n\n".join(parts).strip()
                if page_text:
                    source = str(metadata.get("source", ""))
                    docs.append(
                        Document(
                            content=[TextBlock(text=page_text)],
                            metadata={
                                **metadata,
                                "page_number": i + 1,
                                "total_pages": len(pdf.pages),
                            },
                            id=_page_id(source, i + 1, page_text),
                        )
                    )

        return docs

    async def _load_with_pypdf(
        self,
        source: Union[str, Path, bytes],
        metadata: dict[str, Any],
    ) -> list[Document]:
        from pypdf import PdfReader

        if isinstance(source, bytes):
            reader = PdfReader(io.BytesIO(source))
        else:
            path = Path(source)
            metadata.setdefault("source", str(path))
            reader = PdfReader(path)

        docs: list[Document] = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            if text.strip():
                source = str(metadata.get("source", ""))
                docs.append(
                    Document(
                        content=[TextBlock(text=text)],
                        metadata={
                            **metadata,
                            "page_number": i + 1,
                            "total_pages": len(reader.pages),
                        },
                        id=_page_id(source, i + 1, text),
                    )
                )

        return docs
