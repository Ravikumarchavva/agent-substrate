"""File service — save, list, retrieve and extract text from uploaded files.

Text extraction supports:
  - CSV  → column summary + first 100 rows
  - JSON → pretty-printed
  - TXT / MD / source-code → raw UTF-8
  - PDF  → per-page text via pypdf  (optional dep)
  - XLSX → sheet rows via openpyxl  (optional dep)

Images are not converted to text here; the caller receives a separate
``image_url`` vision block that can be embedded in a multimodal message.
All other binary types are pushed to the code-interpreter VM so the agent
can access them programmatically.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import logging
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_framework.server.models import Element

logger = logging.getLogger(__name__)

# Maximum characters injected per file into the LLM context window
_MAX_TEXT_CHARS = 50_000

_TEXT_MIMES = {
    "text/plain",
    "text/markdown",
    "text/html",
    "text/css",
    "text/javascript",
    "application/javascript",
    "application/x-python",
    "application/x-sh",
    "application/xml",
    "text/xml",
    "text/x-python",
}
_TEXT_EXTS = {
    "txt", "md", "py", "js", "ts", "tsx", "jsx",
    "html", "css", "sh", "yaml", "yml", "toml",
    "ini", "conf", "log", "r", "sql", "xml",
}

_XLSX_MIMES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
}


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------

async def save_file(
    db: AsyncSession,
    *,
    thread_id: uuid.UUID,
    name: str,
    mime: str,
    content: bytes,
) -> Element:
    """Persist an uploaded file to the elements table."""
    element = Element(
        thread_id=thread_id,
        type="file",
        name=name,
        mime=mime,
        size=str(len(content)),
        content=content,
    )
    db.add(element)
    await db.flush()
    return element


async def list_files(
    db: AsyncSession,
    thread_id: uuid.UUID,
) -> list[Element]:
    """Return all files attached to a thread, oldest first."""
    result = await db.execute(
        select(Element)
        .where(Element.thread_id == thread_id, Element.type == "file")
        .order_by(Element.id)
    )
    return list(result.scalars().all())


async def get_file(
    db: AsyncSession,
    file_id: uuid.UUID,
    thread_id: uuid.UUID,
) -> Optional[Element]:
    """Get a single file that belongs to the given thread."""
    result = await db.execute(
        select(Element).where(
            Element.id == file_id,
            Element.thread_id == thread_id,
            Element.type == "file",
        )
    )
    return result.scalar_one_or_none()


async def delete_file(
    db: AsyncSession,
    file_id: uuid.UUID,
    thread_id: uuid.UUID,
) -> bool:
    """Delete a file. Returns True if found and deleted."""
    element = await get_file(db, file_id, thread_id)
    if not element:
        return False
    await db.delete(element)
    await db.flush()
    return True


async def get_files_by_ids(
    db: AsyncSession,
    file_ids: list[uuid.UUID],
    thread_id: uuid.UUID,
) -> list[Element]:
    """Fetch a set of files by ID, all belonging to the same thread."""
    if not file_ids:
        return []
    result = await db.execute(
        select(Element).where(
            Element.id.in_(file_ids),
            Element.thread_id == thread_id,
            Element.type == "file",
        )
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def extract_text(element: Element) -> Optional[str]:
    """Extract a text representation for LLM context injection.

    Returns the extracted string (≤ _MAX_TEXT_CHARS chars), or None when
    the file is binary-only (images / unknown blobs) and cannot be texified.
    """
    if not element.content:
        return None

    mime = (element.mime or "").lower()
    name = element.name or ""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    raw: bytes = element.content

    # ── Plain text / source code ──────────────────────────────────────────
    if mime in _TEXT_MIMES or ext in _TEXT_EXTS:
        try:
            return raw.decode("utf-8", errors="replace")[:_MAX_TEXT_CHARS]
        except Exception:
            return None

    # ── JSON ──────────────────────────────────────────────────────────────
    if mime == "application/json" or ext == "json":
        try:
            parsed = json.loads(raw.decode("utf-8"))
            return json.dumps(parsed, indent=2)[:_MAX_TEXT_CHARS]
        except Exception:
            return raw.decode("utf-8", errors="replace")[:_MAX_TEXT_CHARS]

    # ── CSV ───────────────────────────────────────────────────────────────
    if mime in ("text/csv", "application/csv") or ext == "csv":
        try:
            text = raw.decode("utf-8", errors="replace")
            reader = csv.reader(io.StringIO(text))
            rows = list(reader)
            if not rows:
                return "(empty CSV)"
            headers = rows[0]
            total_rows = len(rows) - 1
            preview = rows[1:101]
            lines: list[str] = [
                f"CSV file: {name}",
                f"Columns ({len(headers)}): {', '.join(headers)}",
                f"Total rows: {total_rows}",
                "",
                ",".join(headers),
            ]
            lines.extend(",".join(str(v) for v in r) for r in preview)
            if total_rows > 100:
                lines.append(f"... ({total_rows - 100} more rows not shown)")
            return "\n".join(lines)[:_MAX_TEXT_CHARS]
        except Exception as exc:
            logger.warning("CSV extraction failed for %s: %s", name, exc)
            return None

    # ── PDF ───────────────────────────────────────────────────────────────
    if mime == "application/pdf" or ext == "pdf":
        try:
            import pypdf  # noqa: PLC0415
            reader_obj = pypdf.PdfReader(io.BytesIO(raw))
            pages: list[str] = []
            for i, page in enumerate(reader_obj.pages):
                pages.append(f"--- Page {i + 1} ---\n{page.extract_text() or ''}")
            return "\n\n".join(pages)[:_MAX_TEXT_CHARS]
        except ImportError:
            logger.warning("pypdf not installed; PDF text extraction unavailable for %s", name)
            return (
                f"(PDF file: {name} — text extraction unavailable, "
                "install pypdf to enable it. "
                "The file is available in the code interpreter at /data/{name})"
            )
        except Exception as exc:
            logger.warning("PDF extraction failed for %s: %s", name, exc)
            return None

    # ── XLSX ──────────────────────────────────────────────────────────────
    if mime in _XLSX_MIMES or ext in ("xlsx", "xls"):
        try:
            import openpyxl  # noqa: PLC0415
            wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            lines: list[str] = []
            for sheet_name in wb.sheetnames:
                ws = wb[sheet_name]
                lines.append(f"=== Sheet: {sheet_name} ===")
                for row_idx, row in enumerate(ws.iter_rows(values_only=True)):
                    lines.append(",".join("" if v is None else str(v) for v in row))
                    if row_idx >= 199:
                        lines.append("... (truncated at 200 rows)")
                        break
            return "\n".join(lines)[:_MAX_TEXT_CHARS]
        except ImportError:
            logger.warning("openpyxl not installed; XLSX text extraction unavailable for %s", name)
            return (
                f"(Excel file: {name} — text extraction unavailable, "
                "install openpyxl to enable it. "
                f"The file is available in the code interpreter at /data/{name})"
            )
        except Exception as exc:
            logger.warning("XLSX extraction failed for %s: %s", name, exc)
            return None

    # ── Images — no text; handled via vision blocks ───────────────────────
    if mime.startswith("image/"):
        return None

    # ── Unknown binary ────────────────────────────────────────────────────
    return None


def to_vision_image_block(element: Element) -> Optional[dict]:
    """Build an OpenAI-compatible vision content block from an image element.

    Returns None if the element is not an image or has no content.
    """
    if not element.content:
        return None
    mime = (element.mime or "image/png").lower()
    if not mime.startswith("image/"):
        return None
    b64 = base64.b64encode(element.content).decode()
    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:{mime};base64,{b64}",
            "detail": "auto",
        },
    }
