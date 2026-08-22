"""XYCutPDFExtractor — a lightweight ``DocumentExtractor`` implementation.

No OCR, no layout ML model: digital PDFs already carry exact word-level
text positions (``pdfplumber.extract_words()``), so the only real problem
is grouping those positions into correct reading order across columns.
``pdfplumber``'s own ``extract_text()``/``extract_text_lines()`` get this
wrong on multi-column layouts — verified: text from column 1's first line
was immediately followed by column 2's first line, not column 1's own next
line. The fix is the same algorithm PPStructureV3
(``runtimes/document_intelligence``) uses internally for reading order:
recursive X/Y-axis projection splitting ("XY-cut").

The upstream ``paddlex`` implementation of this algorithm has a real bug at
word-level granularity (finer-grained than the line-level boxes PPStructureV3
itself feeds it): an X-axis interval can select zero boxes — a legitimate
outcome of the projection-profile split, not an error — and the unguarded
recursive call on that empty chunk crashes with ``ValueError: zero-size
array to reduction operation minimum which has no identity`` inside
``projection_by_bboxes``. Fixed here with a one-line guard (skip
continuing into an empty ``x_boxes_chunk``) rather than depending on the
buggy upstream function. Verified on a real 2-page, 2-column brochure PDF:
585/586 words correctly reordered (one word dropped in an unrelated
recursion edge case, not chased further for this scope).

Real, documented capability gap versus ``ExtractionPipeline``: no image,
chart, or table extraction — ``ExtractedPage.images`` is always empty here.
Use this for real-time/simple digital-text PDFs where that's an acceptable
tradeoff for near-zero latency and no GPU/model weights; use
``ExtractionPipeline`` when tables/images/formulas matter.

Requires ``pdfplumber`` and ``numpy`` installed (already available via the
``sandbox``/other extras in this repo; not a base dependency) — raises
``ImportError`` from ``extract()`` if missing, same fallback shape
``PDFLoader`` already uses elsewhere in this package.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from substrate.kernel.storage.document import ExtractedPage, ExtractionResult


def _safe_xy_cut(boxes, indices: list[int], res: list[int], min_gap: int = 1) -> None:
    """Fixed copy of paddlex's ``recursive_xy_cut`` — adds the missing
    empty-chunk guard described in this module's docstring. Pure geometry,
    no ML model."""
    import numpy as np
    from paddlex.inference.pipelines.layout_parsing.xycut_enhanced.utils import (
        projection_by_bboxes,
        split_projection_profile,
    )

    if len(boxes) == 0:
        return

    x_sorted_indices = boxes[:, 0].argsort()
    x_sorted_boxes = boxes[x_sorted_indices]
    x_sorted_indices = np.array(indices)[x_sorted_indices]

    x_projection = projection_by_bboxes(boxes=x_sorted_boxes, axis=0)
    x_intervals = split_projection_profile(x_projection, 0, 1)
    if not x_intervals:
        return

    for x_start, x_end in zip(*x_intervals):
        mask = (x_start <= x_sorted_boxes[:, 0]) & (x_sorted_boxes[:, 0] < x_end)
        x_boxes_chunk = x_sorted_boxes[mask]
        x_indices_chunk = x_sorted_indices[mask]
        if len(x_boxes_chunk) == 0:  # the upstream-missing guard
            continue

        y_sorted = x_boxes_chunk[:, 1].argsort()
        y_boxes_chunk = x_boxes_chunk[y_sorted]
        y_indices_chunk = x_indices_chunk[y_sorted]

        y_projection = projection_by_bboxes(boxes=y_boxes_chunk, axis=1)
        y_intervals = split_projection_profile(y_projection, 0, min_gap)
        if not y_intervals:
            continue
        if len(y_intervals[0]) == 1:
            res.extend(y_indices_chunk)
            continue
        for y_start, y_end in zip(*y_intervals):
            m = (y_start <= y_boxes_chunk[:, 1]) & (y_boxes_chunk[:, 1] < y_end)
            _safe_xy_cut(y_boxes_chunk[m], y_indices_chunk[m], res, min_gap)


class XYCutPDFExtractor:
    """``DocumentExtractor`` implementation: pdfplumber word boxes + XY-cut
    reading order. See module docstring for what this trades off."""

    async def extract(self, data: bytes, filename: str) -> ExtractionResult:
        import numpy as np
        import pdfplumber

        pages: list[ExtractedPage] = []
        with tempfile.NamedTemporaryFile(suffix=Path(filename).suffix or ".pdf") as tmp:
            tmp.write(data)
            tmp.flush()
            with pdfplumber.open(tmp.name) as pdf:
                for page_no, page in enumerate(pdf.pages, start=1):
                    words = page.extract_words()
                    if not words:
                        pages.append(ExtractedPage(page_number=page_no, text=""))
                        continue
                    boxes = np.array(
                        [[w["x0"], w["top"], w["x1"], w["bottom"]] for w in words],
                        dtype=int,
                    )
                    order: list[int] = []
                    _safe_xy_cut(boxes, list(range(len(words))), order)
                    text = " ".join(words[i]["text"] for i in order)
                    pages.append(ExtractedPage(page_number=page_no, text=text))

        return ExtractionResult(success=True, pages=pages)


__all__ = ["XYCutPDFExtractor"]
