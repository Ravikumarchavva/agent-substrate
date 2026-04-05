"""InvoiceExtractorTool — extract text from invoice PDFs and scanned images.

Supports:
- **PDF** files  → pdfplumber (text layer + table detection)
- **TIF/PNG/JPG** → Pillow + pytesseract OCR

Optional dependency group ``pdf``.  Install with::

    uv sync --group pdf

Tesseract binary is required for image OCR.  Install separately:
- Windows: https://github.com/UB-Mannheim/tesseract/wiki
- Linux:   sudo apt-get install tesseract-ocr
- macOS:   brew install tesseract
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from raavan.core.tools.base_tool import BaseTool, ToolResult, ToolRisk

logger = logging.getLogger(__name__)

_IMAGE_SUFFIXES = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp", ".webp"}
_PDF_SUFFIXES = {".pdf"}


class InvoiceExtractorTool(BaseTool):
    """Extract text and tables from invoice PDFs or scanned image files."""

    def __init__(self) -> None:
        super().__init__(
            name="invoice_extractor",
            description=(
                "Extract text from invoice files. Supports PDFs (via pdfplumber) "
                "and scanned images such as TIF/PNG/JPEG (via pytesseract OCR). "
                "Returns page-by-page text and, for PDFs, optionally any tables found."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": (
                            "Path to the invoice file. PDF, TIF, TIFF, PNG, JPG, "
                            "and JPEG are supported."
                        ),
                    },
                    "pages": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": (
                            "Specific page numbers to extract (0-indexed). "
                            "If omitted, all pages are processed. "
                            "Applies to multi-page PDFs and multi-frame TIFFs."
                        ),
                    },
                    "extract_tables": {
                        "type": "boolean",
                        "description": (
                            "Extract tables separately (PDF only). Defaults to true."
                        ),
                    },
                },
                "required": ["file_path"],
                "additionalProperties": False,
            },
            risk=ToolRisk.SAFE,
            category="data/extraction",
            tags=[
                "invoice",
                "pdf",
                "tif",
                "ocr",
                "extract",
                "table",
                "document",
                "image",
            ],
            aliases=["pdf_extractor", "extract_invoice", "image_extractor"],
        )

    async def execute(  # type: ignore[override]
        self,
        *,
        file_path: str,
        pages: list[int] | None = None,
        extract_tables: bool = True,
    ) -> ToolResult:
        path = Path(file_path)
        if not path.exists():
            return ToolResult(
                content=[{"type": "text", "text": f"File not found: {file_path}"}],
                is_error=True,
            )

        suffix = path.suffix.lower()

        if suffix in _IMAGE_SUFFIXES:
            return self._extract_image(path, pages)
        if suffix in _PDF_SUFFIXES:
            return self._extract_pdf(path, pages, extract_tables)

        return ToolResult(
            content=[
                {
                    "type": "text",
                    "text": (
                        f"Unsupported file type: {path.suffix}. "
                        f"Supported: PDF, TIF, TIFF, PNG, JPG, JPEG."
                    ),
                }
            ],
            is_error=True,
        )

    # ------------------------------------------------------------------
    # Image / OCR path
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_image(path: Path, pages: list[int] | None) -> ToolResult:
        try:
            from PIL import Image
        except ImportError:
            return ToolResult(
                content=[
                    {
                        "type": "text",
                        "text": "Pillow is not installed. Run: uv sync --group pdf",
                    }
                ],
                is_error=True,
            )
        try:
            import pytesseract
        except ImportError:
            return ToolResult(
                content=[
                    {
                        "type": "text",
                        "text": (
                            "pytesseract is not installed. Run: uv sync --group pdf. "
                            "Also ensure the Tesseract binary is installed on your system."
                        ),
                    }
                ],
                is_error=True,
            )

        try:
            img = Image.open(path)
        except Exception as exc:
            return ToolResult(
                content=[{"type": "text", "text": f"Cannot open image: {exc}"}],
                is_error=True,
            )

        # Collect all frames (multi-page TIFF support)
        frames: list[Any] = []
        try:
            while True:
                frames.append(img.copy())
                img.seek(img.tell() + 1)
        except EOFError:
            pass

        if not frames:
            frames = [img]

        # Filter by requested page indices
        if pages is not None:
            selected = [frames[i] for i in pages if 0 <= i < len(frames)]
            if not selected:
                return ToolResult(
                    content=[
                        {
                            "type": "text",
                            "text": (
                                f"No valid pages selected. "
                                f"Image has {len(frames)} frame(s) (0-indexed)."
                            ),
                        }
                    ],
                    is_error=True,
                )
        else:
            selected = frames

        page_texts: list[str] = []
        for idx, frame in enumerate(selected):
            try:
                text = pytesseract.image_to_string(frame)
            except Exception as exc:
                logger.warning("OCR failed on frame %d of %s: %s", idx, path.name, exc)
                text = f"[OCR failed: {exc}]"
            page_texts.append(f"--- Page {idx + 1} ---\n{text.strip()}")

        full_text = "\n\n".join(page_texts)
        max_chars = 100_000
        truncated = len(full_text) > max_chars
        if truncated:
            full_text = (
                full_text[:max_chars]
                + f"\n\n... [truncated, total {len(full_text)} chars]"
            )

        return ToolResult(
            content=[{"type": "text", "text": full_text}],
            app_data={
                "file": str(path),
                "format": "image_ocr",
                "total_pages": len(frames),
                "pages_extracted": len(selected),
                "truncated": truncated,
            },
        )

    # ------------------------------------------------------------------
    # PDF path
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_pdf(
        path: Path, pages: list[int] | None, extract_tables: bool
    ) -> ToolResult:
        try:
            import pdfplumber
        except ImportError:
            return ToolResult(
                content=[
                    {
                        "type": "text",
                        "text": "pdfplumber is not installed. Run: uv sync --group pdf",
                    }
                ],
                is_error=True,
            )

        try:
            return InvoiceExtractorTool._run_pdfplumber(
                pdfplumber, path, pages, extract_tables
            )
        except Exception as exc:
            logger.exception("PDF extraction failed for %s", path)
            return ToolResult(
                content=[{"type": "text", "text": f"PDF extraction error: {exc}"}],
                is_error=True,
            )

    @staticmethod
    def _run_pdfplumber(
        pdfplumber: Any,
        path: Path,
        pages: list[int] | None,
        extract_tables: bool,
    ) -> ToolResult:
        page_texts: list[str] = []
        all_tables: list[dict[str, Any]] = []

        with pdfplumber.open(path) as pdf:
            total_pages = len(pdf.pages)
            target_pages = (
                [pdf.pages[i] for i in pages if 0 <= i < total_pages]
                if pages
                else pdf.pages
            )

            if not target_pages:
                return ToolResult(
                    content=[
                        {
                            "type": "text",
                            "text": (
                                f"No valid pages selected. "
                                f"PDF has {total_pages} pages (0-indexed)."
                            ),
                        }
                    ],
                    is_error=True,
                )

            for page in target_pages:
                text = page.extract_text() or ""
                page_texts.append(f"--- Page {page.page_number} ---\n{text}")

                if extract_tables:
                    for table in page.extract_tables():
                        if table:
                            all_tables.append({"page": page.page_number, "rows": table})

        full_text = "\n\n".join(page_texts)
        max_chars = 100_000
        truncated = len(full_text) > max_chars
        if truncated:
            full_text = (
                full_text[:max_chars]
                + f"\n\n... [truncated, total {len(full_text)} chars]"
            )

        output_parts = [full_text]
        if extract_tables and all_tables:
            table_lines = [f"\n--- Tables ({len(all_tables)} found) ---"]
            for t in all_tables:
                table_lines.append(f"\nPage {t['page']}:")
                for row in t["rows"]:
                    table_lines.append(
                        "  | "
                        + " | ".join(str(cell) if cell else "" for cell in row)
                        + " |"
                    )
            output_parts.append("\n".join(table_lines))

        return ToolResult(
            content=[{"type": "text", "text": "\n".join(output_parts)}],
            app_data={
                "file": str(path),
                "format": "pdf",
                "total_pages": total_pages,
                "pages_extracted": len(target_pages),
                "tables_found": len(all_tables),
                "truncated": truncated,
            },
        )
