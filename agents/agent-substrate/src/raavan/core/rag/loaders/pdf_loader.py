"""PDF document loader using pdfplumber.

Produces one ``Document`` per page with layout-aware text extraction and
optional table extraction.  Falls back to pypdf if pdfplumber fails.

Requires the ``[pdf]`` dependency group: ``uv sync --group pdf``
"""

from __future__ import annotations

import io
import logging
import uuid
from pathlib import Path
from typing import Any, Optional, Union

from raavan.core.rag.loaders.base import BaseDocumentLoader
from raavan.core.rag.vector_store import Document

logger = logging.getLogger(__name__)


class PDFLoader(BaseDocumentLoader):
    """Load PDF files — one ``Document`` per page.

    Uses ``pdfplumber`` for layout-aware text + table extraction.
    Falls back to ``pypdf`` if pdfplumber is not installed.
    """

    def __init__(self, extract_tables: bool = True) -> None:
        self.extract_tables = extract_tables

    async def load(
        self,
        source: Union[str, Path, bytes],
        *,
        metadata: Optional[dict[str, Any]] = None,
    ) -> list[Document]:
        metadata = metadata or {}
        docs: list[Document] = []

        try:
            docs = await self._load_with_pdfplumber(source, metadata)
        except ImportError:
            logger.info("pdfplumber not available, falling back to pypdf")
            docs = await self._load_with_pypdf(source, metadata)

        return docs

    async def _load_with_pdfplumber(
        self,
        source: Union[str, Path, bytes],
        metadata: dict[str, Any],
    ) -> list[Document]:
        import pdfplumber

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
                    docs.append(
                        Document(
                            text=page_text,
                            metadata={
                                **metadata,
                                "page_number": i + 1,
                                "total_pages": len(pdf.pages),
                            },
                            id=str(uuid.uuid4()),
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
                docs.append(
                    Document(
                        text=text,
                        metadata={
                            **metadata,
                            "page_number": i + 1,
                            "total_pages": len(reader.pages),
                        },
                        id=str(uuid.uuid4()),
                    )
                )

        return docs
