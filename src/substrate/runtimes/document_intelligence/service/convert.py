"""Two-tier document conversion for Office formats (DOCX/DOC/PPTX/PPT/ODT/RTF).

Tier 1 is ``markitdown`` (Microsoft, pure Python — DOCX via ``mammoth``, PPTX
via ``python-pptx``, straight to structured Markdown, no rendering engine).
Tier 2 is LibreOffice-headless -> PDF, a fallback used only when Tier 1's
extracted text comes back thin (image-heavy/scanned Office docs) — verified
``markitdown`` has no ``textract``/hosted-API extras pulled in by a scoped
install, so it's cheap to always try first.

``engines/raw_text.py`` is the only caller: it delegates DOCX/PPTX/etc.
extraction here, and (only on a Tier 2 escalation) re-enters its own
pypdfium2 PDF-extraction path on the LibreOffice-converted bytes — imported
lazily inside :func:`convert_office_document` to avoid a module-level import
cycle (``raw_text.py`` imports this module at top level).
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import uuid
from pathlib import Path

from substrate.logger import setup_logging
from substrate.runtimes.document_intelligence.service.types import (
    ExtractedPage,
    ExtractionResult,
)

logger = setup_logging("substrate.document_intelligence.convert")

# Real, standard IANA media types for each format — not guessed casually.
_CONTENT_TYPE_EXT: dict[str, str] = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/ms-powerpoint": ".ppt",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/rtf": ".rtf",
    "text/rtf": ".rtf",
}

CONVERTIBLE_CONTENT_TYPES: set[str] = set(_CONTENT_TYPE_EXT)


def _is_thin(result: ExtractionResult, threshold_chars: int) -> bool:
    """Thin-text heuristic for escalating Tier 1 -> Tier 2.

    Chosen deliberately simple: total extracted characters across the whole
    document below *threshold_chars*. ``markitdown``'s public API returns
    one flat markdown/text string per document with no exposed page/section
    boundaries (unlike pypdfium2's per-page API), so the "more than half of
    the pages are thin" variant of this heuristic has nothing to key off of
    here — Tier 1's result is always represented as a single
    :class:`ExtractedPage` (see ``_tier1_markitdown`` below), so the
    per-document total *is* the per-page total. This is a judgment call, not
    a hard number from the plan.
    """
    total_chars = sum(len(p.text) for p in result.pages)
    return total_chars < threshold_chars


def _tier1_markitdown(data: bytes, filename: str, content_type: str) -> ExtractionResult:
    """Synchronous Tier 1 conversion via ``markitdown``. Never raises — an
    import failure (package not installed) or a conversion error both
    degrade to an empty result, which ``convert_office_document`` then
    treats as "thin" and escalates to Tier 2."""
    try:
        from markitdown import MarkItDown
    except ImportError:
        logger.warning("markitdown not installed — Tier 1 conversion skipped")
        return ExtractionResult(pages=[], markdown="", engine="raw_text")

    ext = Path(filename).suffix or _CONTENT_TYPE_EXT.get(content_type, "")
    text = ""
    try:
        # markitdown dispatches its internal converter by file extension,
        # not content-type — it needs a real file path with the correct
        # suffix, a BytesIO stream alone isn't enough to pick the right
        # converter (DOCX vs PPTX vs ODT all need their own converter).
        with tempfile.NamedTemporaryFile(suffix=ext) as tmp:
            tmp.write(data)
            tmp.flush()
            md = MarkItDown()
            result = md.convert(tmp.name)
            # Verified against markitdown==0.1.7's real DocumentConverterResult:
            # `.markdown` is the real attribute; `.text_content` is a
            # soft-deprecated property that just returns `.markdown` (kept
            # here for older-version compat, not because both are needed).
            text = getattr(result, "text_content", None) or getattr(
                result, "markdown", ""
            )
    except Exception:
        logger.exception("markitdown Tier 1 conversion failed for %s", filename)
        text = ""

    page = ExtractedPage(page_number=1, text=text, markdown=text)
    return ExtractionResult(pages=[page], markdown=text, engine="raw_text")


async def convert_office_document(
    data: bytes,
    filename: str,
    content_type: str,
    *,
    thin_text_threshold_chars: int = 100,
) -> ExtractionResult:
    """Tier 1 (markitdown) first; escalates to Tier 2 (LibreOffice -> PDF,
    caller must then re-run PDF extraction on the result) only if Tier 1's
    text comes back thin. Returns an ExtractionResult either way — if Tier 2
    also fails or isn't available, returns Tier 1's (possibly thin) result
    rather than raising, since some real text is better than none."""
    tier1_result = await asyncio.to_thread(
        _tier1_markitdown, data, filename, content_type
    )
    if not _is_thin(tier1_result, thin_text_threshold_chars):
        return tier1_result

    pdf_bytes = await convert_via_libreoffice(data, filename)
    if pdf_bytes is None:
        return tier1_result

    # Lazy import — avoids a module-level circular import with
    # engines/raw_text.py, which imports this module at top level.
    from substrate.runtimes.document_intelligence.service.engines.raw_text import (
        _extract_pdf,
    )

    return _extract_pdf(pdf_bytes)


async def convert_via_libreoffice(
    data: bytes, filename: str, *, timeout_s: float = 120.0
) -> bytes | None:
    """Tier 2 fallback: ``soffice --headless --convert-to pdf``. Returns the
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


__all__ = [
    "CONVERTIBLE_CONTENT_TYPES",
    "convert_office_document",
    "convert_via_libreoffice",
]
