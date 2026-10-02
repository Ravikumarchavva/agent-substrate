"""PDF → markdown pages with PDFium (``pypdfium2``): the text layer, its headings, figures, and OCR for pages that have no text.

What it does, per document:

* reads every line of every page with its box, font size and weight;
* finds **headings** — from the PDF's own bookmarks when it has them, otherwise from font size and weight (with a guard: if the guess
  yields far too many headings it is discarded and the pages are read as plain paragraphs);
* drops the **running headers and footers** (the same line at the top or bottom of at least half the pages) and page numbers;
* joins lines into **paragraphs** and bullet/numbered lines into **lists**, and un-hyphenates words split across lines;
* extracts large embedded **images** as figures, in reading position;
* runs OCR **only on pages with no readable text** (or on every page for ``strategy="ocr_only"``). With no OCR available such a page is
  returned with ``needs_ocr=True``, never silently empty.

Not done here (a layout model is needed): tables in digital PDFs and multi-column reading order. ``Reader("http://…")`` does those.
"""

from __future__ import annotations

import math
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from substrate.documents.protocols import Ocr
from substrate.documents.reading.build import (
    MAX_IMAGES_PER_PAGE,
    document_markdown,
    join_blocks,
)
from substrate.documents.reading.png import encode_png
from substrate.documents.reading.text import plain_text
from substrate.documents.types import (
    ExtractedImage,
    ExtractedImageLabel,
    ExtractedPage,
    ExtractionResult,
    ReadLimits,
)

PDFIUM_LOCK = (
    threading.RLock()
)  # PDFium is not thread-safe: in-process reads take turns

OCR_DPI = 300
OCR_MAX_PIXELS = 12_000_000
IMAGE_MIN_SIDE = 200
IMAGE_MIN_PAGE_FRACTION = 0.05
IMAGE_MAX_PIXELS = 8_000_000
MAX_IMAGES_PER_DOCUMENT = 60
LOW_OCR_CONFIDENCE = 60.0

_BULLET = re.compile(r"^\s*([•·▪●◦‣∙\-–—*])\s+(?=\S)")
_NUMBERED = re.compile(r"^\s*(\(?\d{1,3}[.)]|[a-zA-Z][.)])\s+(?=\S)")
_DIGITS = re.compile(r"\d+")
_SENTENCE_END = re.compile(r"[.!?:;,)\]]$")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\ufffe\uffff]")


class ReadTimeout(Exception):
    """The wall-clock budget ran out (checked between pages when reading in-process)."""


@dataclass
class _Line:
    text: str
    left: float
    bottom: float
    right: float
    top: float
    size: float
    weight: int
    hyphenated: bool = False
    """The line ends in a hyphen PDFium inserted at a line wrap (``\\x02``): the word continues on the next line."""


@dataclass
class _PageData:
    index: int
    width: float
    height: float
    lines: list[_Line] = field(default_factory=list)
    visible: int = 0
    garbage: int = 0


def _norm(text: str) -> str:
    return _DIGITS.sub("#", " ".join(text.lower().split()))


def _alnum(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", text.lower())


def _page_lines(page: Any, raw: Any) -> _PageData:
    width, height = page.get_size()
    data = _PageData(index=0, width=width, height=height)
    textpage = page.get_textpage()
    try:
        for i in range(textpage.count_rects()):
            left, bottom, right, top = textpage.get_rect(i)
            text = (
                textpage.get_text_bounded(left, bottom, right, top)
                .replace("\r", "")
                .replace("\n", " ")
            )
            hyphenated = text.rstrip().endswith("\x02")
            text = _CONTROL.sub("", text).strip()
            if not text:
                continue
            size, weight = float(top - bottom), 400
            index = raw.FPDFText_GetCharIndexAtPos(
                textpage.raw,
                left + 1.0,
                (bottom + top) / 2,
                4.0,
                max(2.0, top - bottom),
            )
            if index >= 0:
                # the effective size is the font size scaled by the text matrix (many PDFs say "font size 1" and scale it)
                matrix = raw.FS_MATRIX()
                scale = (
                    math.hypot(matrix.c, matrix.d)
                    if raw.FPDFText_GetMatrix(textpage.raw, index, matrix)
                    else 1.0
                )
                measured = raw.FPDFText_GetFontSize(textpage.raw, index) * scale
                size = float(measured) if measured and measured >= 3 else size
                w = raw.FPDFText_GetFontWeight(textpage.raw, index)
                weight = int(w) if w and w > 0 else 400
            data.lines.append(
                _Line(
                    text, left, bottom, right, top, round(size, 1), weight, hyphenated
                )
            )
            data.visible += sum(1 for ch in text if not ch.isspace())
            data.garbage += sum(1 for ch in text if ch == "�" or "" <= ch <= "")
    finally:
        textpage.close()
    return data


def _body_size(pages: list[_PageData]) -> float:
    counts: Counter[float] = Counter()
    for page in pages:
        for line in page.lines:
            counts[round(line.size * 2) / 2] += len(line.text)
    return counts.most_common(1)[0][0] if counts else 0.0


def _running_lines(pages: list[_PageData]) -> set[tuple[int, int]]:
    """``(page index, line index)`` of the repeated headers/footers and page numbers: the same normalised line in the top or bottom
    8% of at least half the pages (and at least three)."""
    if len(pages) < 3:
        return set()
    seen: dict[str, set[int]] = {}
    for page in pages:
        for line in page.lines:
            margin = page.height * 0.08
            if line.top >= page.height - margin or line.bottom <= margin:
                seen.setdefault(_norm(line.text), set()).add(page.index)
    repeated = {
        key for key, where in seen.items() if len(where) >= max(3, len(pages) // 2)
    }
    out: set[tuple[int, int]] = set()
    for page in pages:
        margin = page.height * 0.08
        for n, line in enumerate(page.lines):
            if (line.top >= page.height - margin or line.bottom <= margin) and _norm(
                line.text
            ) in repeated:
                out.add((page.index, n))
    return out


def _bookmark_levels(
    doc: Any, pages: list[_PageData]
) -> tuple[dict[tuple[int, str], int], dict[tuple[int, str], str]]:
    """``({(page index, normalised title): level}, {same key: the title as written})`` from the PDF's outline."""
    levels: dict[tuple[int, str], int] = {}
    titles: dict[tuple[int, str], str] = {}
    try:
        for item in doc.get_toc():
            written = (item.get_title() or "").strip()
            dest = item.get_dest()
            page_index = dest.get_index() if dest is not None else None
            title = _alnum(written)
            if title and page_index is not None and 0 <= page_index < len(pages):
                levels[(page_index, title)] = min(6, int(item.level) + 1)
                titles[(page_index, title)] = written
    except Exception:  # noqa: BLE001 — a broken outline is just no outline
        return {}, {}
    return levels, titles


def _size_levels(
    pages: list[_PageData], body: float, skip: set[tuple[int, int]]
) -> dict[float, int]:
    """Heading level by (rounded) font size, for lines that look like headings; empty when the guess is implausible."""
    candidates: Counter[float] = Counter()
    words = 0
    for page in pages:
        for n, line in enumerate(page.lines):
            words += len(line.text.split())
            if (page.index, n) in skip:
                continue
            if _is_heading_shape(line, body):
                candidates[round(line.size * 2) / 2] += 1
    if not candidates or sum(candidates.values()) > max(6, words // 50):
        return {}
    ranked = sorted(candidates, reverse=True)[:3]
    return {size: level for level, size in enumerate(ranked, start=1)}


def _is_heading_shape(line: _Line, body: float) -> bool:
    text = line.text.strip()
    if (
        not text
        or len(text.split()) > 12
        or _SENTENCE_END.search(text)
        or text.isdigit()
        or _BULLET.match(text)
    ):
        return False
    bigger = body > 0 and line.size >= body * 1.15
    bold = line.weight >= 600 and body > 0 and line.size >= body * 0.98
    return bigger or (bold and len(text.split()) <= 8 and len(text) > 2)


def _render(page: Any, *, scale: float) -> Any:
    return page.render(scale=scale, grayscale=True)


def _ocr_png(page: Any, width: float, height: float) -> bytes:
    scale = min(OCR_DPI / 72, math.sqrt(OCR_MAX_PIXELS / max(1.0, width * height)))
    bitmap = _render(page, scale=scale)
    try:
        return encode_png(
            bitmap.width,
            bitmap.height,
            bytes(bitmap.buffer),
            mode="L",
            stride=bitmap.stride,
        )
    finally:
        bitmap.close()


def _paragraphs(lines: list[_Line]) -> list[str]:
    """Group consecutive lines into paragraphs (lists as their own blocks), un-hyphenating across line ends."""
    blocks: list[str] = []
    current: list[str] = []
    previous: _Line | None = None

    def flush() -> None:
        nonlocal current
        if current:
            blocks.append(" ".join(current))
            current = []

    for line in lines:
        text = line.text.strip()
        if _BULLET.match(text) or _NUMBERED.match(text):
            flush()
            marker = (
                _BULLET.sub("- ", text, count=1)
                if _BULLET.match(text)
                else re.sub(r"^\s*\(?(\d{1,3})[.)]\s+", r"\1. ", text, count=1)
            )
            blocks.append(marker)
            previous = line
            continue
        gap = (previous.bottom - line.top) if previous else 0.0
        new_paragraph = (
            previous is None
            or line.top > previous.top + 2
            or (gap > max(previous.size, line.size) * 0.75 and not previous.hyphenated)
        )
        if new_paragraph:
            if (
                blocks
                and blocks[-1].startswith(("- ", "1. "))
                and previous is not None
                and gap <= max(previous.size, line.size) * 0.75
                and line.left > previous.left
            ):
                blocks[-1] += " " + text  # a wrapped list item
                previous = line
                continue
            flush()
        if current and previous is not None and previous.hyphenated:
            current[-1] = (
                current[-1] + text
            )  # a word split across lines: no space, and the hyphen was only the wrap
        else:
            current.append(text)
        previous = line
    flush()
    return blocks


def _picture(
    page: Any, obj: Any, raw: Any, page_area: float
) -> tuple[bytes, int, int, float, float] | None:
    """A large embedded image as ``(PNG, width, height, top y, share of the page it covers)``, or ``None`` if it is small, huge or unreadable."""
    try:
        left, bottom, right, top = obj.get_bounds()
        coverage = (right - left) * (top - bottom) / max(1.0, page_area)
        if coverage < IMAGE_MIN_PAGE_FRACTION:
            return None
        bitmap = obj.get_bitmap(render=False)
    except Exception:  # noqa: BLE001
        return None
    try:
        width, height = bitmap.width, bitmap.height
        if (
            width < IMAGE_MIN_SIDE
            or height < IMAGE_MIN_SIDE
            or width * height > IMAGE_MAX_PIXELS
        ):
            return None
        buffer, stride, fmt = bytes(bitmap.buffer), bitmap.stride, bitmap.format
        if fmt == raw.FPDFBitmap_Gray:
            return (
                encode_png(width, height, buffer, mode="L", stride=stride),
                width,
                height,
                top,
                coverage,
            )
        depth = {
            raw.FPDFBitmap_BGR: 3,
            raw.FPDFBitmap_BGRx: 4,
            raw.FPDFBitmap_BGRA: 4,
        }.get(fmt)
        if depth is None:
            return None
        rgb = bytearray(width * height * 3)
        for y in range(height):
            row = buffer[y * stride : y * stride + width * depth]
            base = y * width * 3
            rgb[base : base + width * 3 : 3] = row[2::depth][:width]
            rgb[base + 1 : base + width * 3 : 3] = row[1::depth][:width]
            rgb[base + 2 : base + width * 3 : 3] = row[0::depth][:width]
        return (
            encode_png(width, height, bytes(rgb), mode="RGB"),
            width,
            height,
            top,
            coverage,
        )
    except Exception:  # noqa: BLE001
        return None
    finally:
        bitmap.close()


def read_pdf(
    data: bytes,
    *,
    limits: ReadLimits | None = None,
    ocr: Ocr | None = None,
    strategy: str = "auto",
    languages: tuple[str, ...] = ("eng",),
    deadline: float | None = None,
) -> ExtractionResult:
    """Read a PDF. Never raises for a document it cannot read (``ReadTimeout`` aside, which the caller maps to a failure)."""
    limits = limits or ReadLimits()
    with PDFIUM_LOCK:
        return _read(data, limits, ocr, strategy, languages, deadline)


def _open(data: bytes) -> tuple[Any, ExtractionResult | None]:
    import pypdfium2 as pdfium  # noqa: PLC0415 — lazy: importing the engine must not load PDFium

    try:
        return pdfium.PdfDocument(data), None
    except pdfium.PdfiumError as exc:
        reason = (
            "the PDF is password-protected"
            if "password" in str(exc).lower()
            else f"not a readable PDF: {exc}"
        )
        return None, ExtractionResult(
            success=False, error=reason, engine="pdfium", content_type="application/pdf"
        )


def _read(
    data: bytes,
    limits: ReadLimits,
    ocr: Ocr | None,
    strategy: str,
    languages: tuple[str, ...],
    deadline: float | None,
) -> ExtractionResult:
    import pypdfium2.raw as raw  # noqa: PLC0415

    def check() -> None:
        if deadline is not None and time.monotonic() > deadline:
            raise ReadTimeout

    doc, failure = _open(data)
    if failure is not None:
        return failure
    warnings: list[str] = []
    handles: list[Any] = []
    try:
        count = len(doc)
        if count == 0:
            return ExtractionResult(
                success=False,
                error="the PDF has no pages",
                engine="pdfium",
                content_type="application/pdf",
            )
        if count > limits.max_pages:
            warnings.append(
                f"only the first {limits.max_pages} of {count} pages were read"
            )
            count = limits.max_pages
        pages: list[_PageData] = []
        for index in range(count):
            check()
            page = doc[index]
            handles.append(page)
            meta = _page_lines(page, raw)
            meta.index = index
            pages.append(meta)
        body = _body_size(pages)
        running = _running_lines(pages)
        for meta in pages:  # what a page really says, without its running header/footer
            meta.visible = sum(
                len(line.text.replace(" ", ""))
                for n, line in enumerate(meta.lines)
                if (meta.index, n) not in running
            )
        bookmarks, bookmark_titles = _bookmark_levels(doc, pages)
        levels = {} if bookmarks else _size_levels(pages, body, running)
        if (
            not bookmarks
            and not levels
            and sum(
                1 for p in pages for line in p.lines if _is_heading_shape(line, body)
            )
            > 6
        ):
            warnings.append(
                "heading detection was switched off: far too many lines looked like headings"
            )

        results: list[ExtractedPage] = []
        used_ocr: str | None = None
        ocr_pages = 0
        low_confidence: list[int] = []
        image_total = 0
        first_heading: str | None = None
        for number, (page, meta) in enumerate(
            zip(handles, pages, strict=True), start=1
        ):
            check()
            objects = list(page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE]))
            area = max(1.0, meta.width * meta.height)

            def coverage(obj: Any, area: float = area) -> float:
                try:
                    left, bottom, right, top = obj.get_bounds()
                    return (right - left) * (top - bottom) / area
                except Exception:  # noqa: BLE001
                    return 0.0

            scanned = any(coverage(o) >= 0.5 for o in objects)
            garbled = (
                meta.garbage > 0
                and meta.garbage / max(1, meta.visible + meta.garbage) > 0.3
            )
            wants_ocr = (
                strategy == "ocr_only" or garbled or (meta.visible < 20 and scanned)
            )
            method, needs_ocr, blocks = "text", False, []
            if (
                wants_ocr
                and strategy != "fast"
                and ocr is not None
                and ocr_pages < limits.max_ocr_pages
            ):
                ocr_pages += 1
                result = ocr.recognize(
                    _ocr_png(page, meta.width, meta.height), languages=languages
                )
                used_ocr = ocr.name
                if result.error:
                    warnings.append(f"OCR failed on page {number}: {result.error}")
                    needs_ocr = True
                else:
                    method = "ocr"
                    blocks = [
                        p
                        for p in (
                            " ".join(chunk.split("\n")).strip()
                            for chunk in result.text.split("\n\n")
                        )
                        if p
                    ]
                    if result.text.strip() and result.confidence < LOW_OCR_CONFIDENCE:
                        low_confidence.append(number)
            elif wants_ocr and strategy != "ocr_only":
                needs_ocr = True
                if strategy != "fast" and ocr is not None:
                    warnings.append(
                        f"page {number} was not OCR'd: the {limits.max_ocr_pages}-page OCR limit was reached"
                    )
            if method == "text":
                skip = {n for (p, n) in running if p == meta.index}
                blocks = _heading_blocks(
                    meta, body, levels, bookmarks, bookmark_titles, skip
                )
                if first_heading is None:
                    first_heading = next(
                        (b[2:].strip() for b in blocks if b.startswith("# ")), None
                    )
            images: list[ExtractedImage] = []
            if image_total < MAX_IMAGES_PER_DOCUMENT:
                found: list[tuple[float, bytes]] = []
                for obj in objects:
                    if len(found) >= MAX_IMAGES_PER_PAGE:
                        break
                    if method == "ocr" and coverage(obj) >= 0.9:
                        continue  # the scan of this very page: its words are already in the text
                    picture = _picture(page, obj, raw, area)
                    if picture is not None:
                        found.append((picture[3], picture[0]))
                for n, (_top, png) in enumerate(
                    sorted(found, key=lambda f: -f[0]), start=1
                ):
                    ident = f"img-p{number}-{n}"
                    images.append(
                        ExtractedImage(
                            data=png,
                            media_type="image/png",
                            page_number=number,
                            label=ExtractedImageLabel.FIGURE,
                            id=ident,
                        )
                    )
                    blocks.append(f"![figure](cid:{ident})")
                    image_total += 1
            markdown = join_blocks(blocks)
            results.append(
                ExtractedPage(
                    page_number=number,
                    text=plain_text(markdown),
                    markdown=markdown,
                    images=images,
                    method=method,  # type: ignore[arg-type]
                    needs_ocr=needs_ocr,
                )
            )
        if low_confidence:
            warnings.append(
                f"low OCR confidence on page(s) {', '.join(map(str, low_confidence[:10]))}: the text may contain errors"
            )
        if any(p.needs_ocr for p in results):
            if strategy == "fast":
                warnings.append(
                    "strategy='fast' does not run OCR: scanned pages are listed in needs_ocr"
                )
            elif ocr is None:
                warnings.append(
                    "some pages are scans with no text layer and no OCR engine is installed (apt install tesseract-ocr, or pip install 'agent-substrate[ocr]')"
                )
        try:
            metadata_title = (doc.get_metadata_value("Title") or "").strip()
        except Exception:  # noqa: BLE001
            metadata_title = ""
        return ExtractionResult(
            pages=results,
            markdown=document_markdown(results),
            engine="pdfium" + (f"+{used_ocr}" if used_ocr else ""),
            content_type="application/pdf",
            title=metadata_title or first_heading,
            warnings=warnings,
        )
    finally:
        for page in handles:
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass
        doc.close()


def _heading_blocks(
    meta: _PageData,
    body: float,
    levels: dict[float, int],
    bookmarks: dict[tuple[int, str], int],
    titles: dict[tuple[int, str], str],
    skip: set[int],
) -> list[str]:
    """The page's text as markdown blocks: headings (bookmarks, else by size), paragraphs and lists."""
    blocks: list[str] = []
    run: list[_Line] = []

    def flush() -> None:
        nonlocal run
        blocks.extend(_paragraphs(run))
        run = []

    matched: set[str] = set()
    for n, line in enumerate(meta.lines):
        if n in skip:
            continue
        level = 0
        key = _alnum(line.text)
        if bookmarks:
            for (page_index, title), lvl in bookmarks.items():
                if (
                    page_index == meta.index
                    and key
                    and (
                        key == title
                        or (len(key) >= 6 and title.startswith(key))
                        or (len(title) >= 6 and key.startswith(title))
                    )
                ):
                    level = lvl
                    matched.add(title)
                    break
        elif levels and _is_heading_shape(line, body):
            level = levels.get(round(line.size * 2) / 2, 0)
        if level:
            flush()
            blocks.append("#" * level + " " + line.text.strip())
        else:
            run.append(line)
    flush()
    if bookmarks:
        # outline entries that start on this page but were not found among its lines still mark where the section begins
        missing = [
            (titles.get((meta.index, t), t), lvl)
            for (p, t), lvl in bookmarks.items()
            if p == meta.index and t not in matched
        ]
        blocks = ["#" * lvl + " " + name for name, lvl in missing] + blocks
    return blocks


__all__ = ["PDFIUM_LOCK", "ReadTimeout", "read_pdf"]
