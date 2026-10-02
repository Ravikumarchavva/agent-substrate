"""Legacy Office formats (``.doc``, ``.ppt``, ``.xls``, ``.rtf``) → PDF with LibreOffice headless.

Everything modern — DOCX, PPTX, XLSX, ODF, HTML, Markdown, text — is read by the library's own reader, natively. LibreOffice is here only for
the binary formats nothing else reads: the file is converted to a PDF, which then goes down the normal PDF path. A pod without
LibreOffice answers those formats with a clear failure rather than guessing.
"""

from __future__ import annotations

import logging

import asyncio
import shutil
import tempfile
import uuid
from pathlib import Path

from substrate.documents.reading.sniff import Format, sniff

logger = logging.getLogger(__name__)


def is_legacy(data: bytes, filename: str, content_type: str | None) -> bool:
    """A binary Office file (OLE: ``.doc``, ``.ppt``, ``.xls``) or RTF — decided from the bytes, like everything else the reader sees."""
    return (
        data[:5] == b"{\\rtf" or sniff(data, filename, content_type or "") == Format.OLE
    )


async def convert_via_libreoffice(
    data: bytes, filename: str, *, timeout_s: float = 120.0
) -> bytes | None:
    """``soffice --headless --convert-to pdf``. Returns the
    converted PDF bytes, or ``None`` if LibreOffice isn't installed / the
    conversion failed / timed out — NEVER raises, this is a best-effort
    fallback."""
    ext = Path(filename).suffix or ".docx"
    in_dir = tempfile.mkdtemp(prefix="soffice-in-")
    out_dir = tempfile.mkdtemp(prefix="soffice-out-")
    try:
        input_path = Path(in_dir) / f"input{ext}"
        input_path.write_bytes(data)

        # Concurrent soffice invocations sharing a profile directory
        # deadlock — a known, guaranteed-to-occur issue under real
        # concurrency, not hypothetical. A fresh profile dir per call
        # avoids it.
        profile_url = f"file:///tmp/soffice-{uuid.uuid4().hex}"

        try:
            proc = await asyncio.create_subprocess_exec(
                "soffice",
                "--headless",
                f"-env:UserInstallation={profile_url}",
                "--convert-to",
                "pdf",
                "--outdir",
                out_dir,
                str(input_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            logger.info("soffice binary not found — Tier 2 conversion unavailable")
            return None

        try:
            await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except TimeoutError:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except TimeoutError:
                proc.kill()
            logger.warning("soffice conversion timed out for %s", filename)
            return None

        if proc.returncode != 0:
            logger.warning(
                "soffice conversion failed for %s (exit %s)", filename, proc.returncode
            )
            return None

        # LibreOffice's own naming convention: same basename, .pdf extension.
        out_path = Path(out_dir) / f"{input_path.stem}.pdf"
        if not out_path.exists():
            return None
        return out_path.read_bytes()
    except Exception:
        logger.exception("soffice conversion errored for %s", filename)
        return None
    finally:
        shutil.rmtree(in_dir, ignore_errors=True)
        shutil.rmtree(out_dir, ignore_errors=True)


__all__ = ["is_legacy", "convert_via_libreoffice"]
