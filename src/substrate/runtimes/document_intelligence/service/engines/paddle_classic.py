"""PaddleOCR-based layout/chart extraction — mode ``ocr_classic``.

Moved verbatim from the old single-engine ``pipeline.py`` (now a back-compat
re-export shim) as part of the 3-mode redesign. Behavior is byte-for-byte
unchanged: still ``PPStructureV3`` (layout + chart/table detection + OCR,
no vision-language model), still tiny/small/medium OCR sizing, still the
same ``max_pages_per_call`` chunking. Kept as an unadvertised fallback mode
behind the same :class:`PaginatedExtractionEngine` Protocol the new VL
engines implement — proves the abstraction is genuinely pluggable (not
just theoretically so), and is the real escape hatch if the VL path ever
regresses on a document type this session didn't test.

All shapes here are verified against a real ``paddleocr==3.7.0`` /
``paddlex==3.7.2`` install — not from documentation. In particular:
``PPStructureV3.predict()`` yields one dict per page with a
``parsing_res_list`` of ``LayoutBlock`` objects (``.label``, ``.content``,
and — only for chart/table blocks — ``.image["img"]``, a real cropped
``PIL.Image``, no manual bbox math needed).

``_disable_mkldnn()``/``_parallelize_crop_image_regions()`` are imported by
``engines/paddle_vl.py`` too — the VL engine's layout-detection stage is
still these same classic Paddle models, so both the non-AVX-512 crash
workaround and the sequential-crop-loop speedup apply there as well.
"""

from __future__ import annotations

import contextlib
import io
import re
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

from substrate.runtimes.document_intelligence.service.types import (
    ExtractedImage,
    ExtractedPage,
    ExtractionResult,
)

# Layout regions extracted as discrete image crops (see docs/capabilities/08-document-intelligence.md)
_IMAGE_LABELS = {"chart", "table", "figure", "image"}

# Minimum confidence required to extract region as an image crop
_MIN_IMAGE_CONFIDENCE = 0.7


def _pdf_page_count(data: bytes) -> int | None:
    """Page count for chunking decisions in ``extract()`` — ``None`` (not
    an exception) for anything that isn't a readable PDF, so a corrupt or
    non-PDF file just skips chunking and goes through the normal single
    predict() path, which already has its own error handling."""
    import pypdfium2 as pdfium

    try:
        doc = pdfium.PdfDocument(data)
    except Exception:
        return None
    try:
        return len(doc)
    finally:
        doc.close()


def _disable_mkldnn() -> None:
    """Work around a real, reproducible paddlepaddle bug: every
    object-detection-style model (the layout detector here) crashes on
    CPUs without AVX-512 with ``NotImplementedError:
    ConvertPirAttribute2RuntimeAttribute not support [...DoubleAttribute]``.
    Confirmed on paddlepaddle 3.3.0 (official CPU index) and 3.3.1 (PyPI)
    alike. There is no public flag/env var for this in paddlex yet — this
    patches its own availability check, which is the only fix found to
    actually work. Must run before constructing any PaddleOCR/paddlex object.
    """
    import paddlex.inference.models.runners.paddle_static.config.pp_option as pp_option

    pp_option.is_mkldnn_available = lambda: False


# Shared across every pipeline in this process — one pool, not one per
# pipeline instance or per call, so repeated construction (tests, multiple
# VL workers in the same process) doesn't leak threads.
_CROP_POOL: Any = None


def _parallelize_crop_image_regions(max_workers: int = 32) -> None:
    """Real, measured bottleneck, found via cProfile on a real 27-page
    document: PaddleX's own ``CropByPolys.__call__``
    (paddlex/inference/pipelines/components/common/crop_image_regions.py)
    crops every detected text region SEQUENTIALLY, one ``cv2.warpPerspective``
    call at a time — 3767 calls consumed 63.2 of 89.4 total extraction
    seconds (71%) in a single Python-level loop on one thread, while every
    other CPU thread on the host and the GPU itself sat idle. This is why
    the sawtooth GPU-utilization pattern documented elsewhere in this file
    is a red herring for the *real* bottleneck — most of the wall time
    isn't GPU-bound at all.

    ``cv2``'s C++ implementation releases the GIL, so parallelizing this
    loop over a thread pool is safe — verified: same image/text counts as
    the sequential version, 3.1x faster on the same file (89.4s -> 28.4s).
    Patches the hot loop directly since there's no public config knob for
    this in paddlex; must run before constructing any PaddleOCR/paddlex
    object, same as ``_disable_mkldnn()`` above.
    """
    global _CROP_POOL
    import copy
    from concurrent.futures import ThreadPoolExecutor

    import numpy as np
    from paddlex.inference.pipelines.components.common.crop_image_regions import (
        CropByPolys,
    )

    if _CROP_POOL is None:
        _CROP_POOL = ThreadPoolExecutor(max_workers=max_workers)
    pool = _CROP_POOL

    def _parallel_call(self: Any, img: Any, dt_polys: list) -> list:
        if self.det_box_type == "quad":
            dt_boxes = np.array(dt_polys)
            boxes = [copy.deepcopy(dt_boxes[bno]) for bno in range(len(dt_boxes))]
            return list(pool.map(lambda b: self.get_minarea_rect_crop(img, b), boxes))
        elif self.det_box_type == "poly":
            boxes = [copy.deepcopy(dt_polys[bno]) for bno in range(len(dt_polys))]
            return list(
                pool.map(lambda b: self.get_poly_rect_crop(img.copy(), b), boxes)
            )
        else:
            raise NotImplementedError

    CropByPolys.__call__ = _parallel_call


_OCR_MODELS = {
    "tiny": ("PP-OCRv6_tiny_det", "PP-OCRv6_tiny_rec"),
    "small": ("PP-OCRv6_small_det", "PP-OCRv6_small_rec"),
    "medium": ("PP-OCRv6_medium_det", "PP-OCRv6_medium_rec"),
}


class _TableHTMLToMarkdown(HTMLParser):
    """Minimal ``<table>`` → GFM markdown-table converter for PP-StructureV3's
    table-recognition output (``table_res.html["pred"]``, surfaced on
    ``block.content`` once ``use_table_recognition=True``). Stdlib-only —
    no new dependency for what's a simple, well-formed HTML shape (PaddleX's
    own tables have no nested tables and rarely use colspan/rowspan; spans
    are not reconstructed, the cell text is just kept in its one cell)."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def to_markdown(self) -> str:
        if not self.rows:
            return ""
        width = max(len(r) for r in self.rows)
        rows = [r + [""] * (width - len(r)) for r in self.rows]
        lines = [
            "| " + " | ".join(rows[0]) + " |",
            "| " + " | ".join(["---"] * width) + " |",
        ]
        lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
        return "\n".join(lines)


def _html_table_to_markdown(html: str) -> str:
    """Best-effort HTML table → markdown. Never raises — a malformed table
    falls back to the flattened plain text rather than dropping the block."""
    try:
        parser = _TableHTMLToMarkdown()
        parser.feed(html)
        md = parser.to_markdown()
        if md:
            return md
    except Exception:
        pass
    return re.sub(r"<[^>]+>", " ", html).strip()


def _rewrite_markdown_images(
    markdown_texts: str, kept: dict[str, str], dropped: set[str]
) -> str:
    """Rewrite PaddleX's native ``<img src="{synthetic_path}">`` tags:
    *kept* paths (cleared ``_MIN_IMAGE_CONFIDENCE`` and became a cropped
    ``ExtractedImage``) get ``src="cid:{id}"``, resolvable against
    ``ExtractResponse.images`` by id; *dropped* paths (confidence-gated
    out, or an image-carrying block outside ``_IMAGE_LABELS``) have their
    whole rendered wrapper removed so no filesystem-relative, unresolvable
    path leaks into the response. Never raises — a regex miss on an
    unexpected wrapper shape just leaves that one image cosmetically
    visible rather than failing the request, consistent with this file's
    existing "never raises" extraction philosophy."""
    for path, img_id in kept.items():
        markdown_texts = markdown_texts.replace(f'src="{path}"', f'src="cid:{img_id}"')
    for path in dropped:
        try:
            # format_image_scaled_by_html's real output shape:
            # <div style="text-align: center;"><img src="{path}" alt="Image"
            # width="N%" /></div>\n — strip the whole wrapper, not just the tag.
            wrapped = re.compile(
                r"<div[^>]*>\s*<img[^>]*src=\""
                + re.escape(path)
                + r"\"[^>]*/?>\s*</div>\n*"
            )
            new_text, n = wrapped.subn("", markdown_texts)
            if n:
                markdown_texts = new_text
                continue
            bare = re.compile(r'<img[^>]*src="' + re.escape(path) + r'"[^>]*/?>')
            markdown_texts = bare.sub("", markdown_texts)
        except re.error:
            continue
    return markdown_texts


def _pages_from_results_for_pipeline(
    results: Iterable[Any], *, page_offset: int = 0
) -> tuple[list[ExtractedPage], list[dict[str, Any]]]:
    """Turn a ``predict()`` results iterable for ONE document's pages into
    ``(pages, markdown_pages)`` — shared by ``PaddleClassicEngine``
    (``extract()``/``extract_batch()``, which demuxes a multi-document
    batch back into per-document result groups before calling this) AND
    ``engines/paddle_vl.py``'s ``PaddleVLEngine`` — verified this session
    the two pipelines yield the same result shape (same PaddleX
    layout-parsing pipeline family), so this is a genuine, not
    coincidental, code-reuse opportunity rather than two engines that
    happen to look similar.

    ``markdown_pages`` is the raw per-page structure
    ``concatenate_markdown_pages`` needs, not yet joined — ``extract()``
    accumulates it across page-chunks (see ``_extract_pdf_in_chunks``) and
    joins once at the end, so paragraph continuation across a chunk
    boundary is identical to one predict() call over the whole document.
    ``page_offset`` shifts ``page_index`` (always 0-based *within whatever
    was predict()-ed*) back to the real page number in the source
    document, for a page range chunk that isn't the document's first."""
    pages: list[ExtractedPage] = []
    markdown_pages: list[dict[str, Any]] = []
    for res in results:
        page_no = page_offset + int(res.get("page_index") or 0) + 1
        # Match blocks to nearest bounding box to recover detector confidence scores
        score_by_bbox = _score_lookup(res.get("layout_det_res"))

        text_parts: list[str] = []
        images: list[ExtractedImage] = []
        kept_image_paths: dict[str, str] = {}
        dropped_image_paths: set[str] = set()
        for block in res.get("parsing_res_list") or []:
            label = getattr(block, "label", "") or ""
            confidence = _nearest_score(score_by_bbox, getattr(block, "bbox", None))
            raw_content = (getattr(block, "content", "") or "").strip()
            # Convert structured table HTML to Markdown for plain-text search stream
            md_table = (
                _html_table_to_markdown(raw_content) if label == "table" else ""
            )
            img_dict = getattr(block, "image", None)
            img_path = img_dict.get("path") if isinstance(img_dict, dict) else None
            pil_img = img_dict.get("img") if isinstance(img_dict, dict) else None

            # Crop as image if label matches, confidence clears threshold, and PIL image exists
            keep_as_image = (
                label in _IMAGE_LABELS
                and confidence >= _MIN_IMAGE_CONFIDENCE
                and pil_img is not None
            )
            if keep_as_image and pil_img is not None:
                buf = io.BytesIO()
                pil_img.convert("RGB").save(buf, format="PNG")
                caption = md_table or raw_content or None
                img_id = f"img-p{page_no}-{len(images)}"
                images.append(
                    ExtractedImage(
                        data=buf.getvalue(),
                        page_number=page_no,
                        label=label,
                        confidence=confidence,
                        caption=caption,
                        id=img_id,
                    )
                )
                if img_path:
                    kept_image_paths[img_path] = img_id
                if md_table:
                    text_parts.append(md_table)
                continue
            if img_path:
                dropped_image_paths.add(img_path)
            # Non-image blocks or marginal detections fall through to plain text
            content = md_table or raw_content
            if content:
                text_parts.append(content)

        # Assemble page markdown using PaddleX reading order
        page_md = res.markdown
        page_markdown_text = _rewrite_markdown_images(
            page_md.get("markdown_texts", ""), kept_image_paths, dropped_image_paths
        )
        # Retain only compressed ExtractedImage.data (drop PIL copies to save memory)
        markdown_pages.append(
            {
                "markdown_texts": page_markdown_text,
                "page_continuation_flags": page_md.get(
                    "page_continuation_flags", (True, True)
                ),
            }
        )

        pages.append(
            ExtractedPage(
                page_number=page_no,
                text="\n\n".join(text_parts),
                images=images,
                markdown=page_markdown_text,
            )
        )

    return pages, markdown_pages


def _finalize_markdown_for_pipeline(
    pipeline: Any, pages: list[ExtractedPage], markdown_pages: list[dict[str, Any]]
) -> str:
    """Join every page's markdown into one document, via PaddleX's own
    CJK-aware ``concatenate_markdown_pages`` (paragraph continuation across
    a page break) when possible, falling back to a plain join if that ever
    raises. *pipeline* is whatever real PaddleX pipeline object produced
    *markdown_pages* — both ``PPStructureV3`` and ``PaddleOCR-VL-1.6``
    pipeline objects expose the same ``concatenate_markdown_pages`` method,
    verified this session."""
    if not markdown_pages:
        return ""
    try:
        return pipeline.concatenate_markdown_pages(markdown_pages).get(
            "markdown_texts", ""
        )
    except Exception:
        return "\n\n".join(p.markdown for p in pages)


class PaddleClassicEngine:
    """One PaddleOCR ``PPStructureV3`` pipeline: layout + chart/table
    detection + OCR, in a single call per document. Mode ``ocr_classic``.
    Implements ``PaginatedExtractionEngine`` (async wrappers below wrap the
    same sync, CPU/GPU-bound calls this file always had — the engine itself
    doesn't change, only how ``routes.py`` calls it)."""

    name = "ppstructurev3"

    def __init__(
        self,
        *,
        ocr_size: str = "tiny",
        device: str = "cpu",
        ocr_batch_size: int = 16,
        max_pages_per_call: int | None = None,
    ) -> None:
        _disable_mkldnn()
        _parallelize_crop_image_regions()

        from paddleocr import PPStructureV3

        det_model, rec_model = _OCR_MODELS[ocr_size]
        # Configure batch size on GPU to avoid sawtooth single-region CUDA launches
        batch_size = ocr_batch_size if device.startswith("gpu") else None
        self._pipeline = PPStructureV3(
            text_detection_model_name=det_model,
            text_recognition_model_name=rec_model,
            device=device,
            text_recognition_batch_size=batch_size,
            textline_orientation_batch_size=batch_size,
            # Enable SLANet table structure recognition for HTML table output
            use_table_recognition=True,
            use_formula_recognition=False,
            use_seal_recognition=False,
            use_chart_recognition=False,
        )
        # See ServiceConfig.max_pages_per_call's docstring — bounds a
        # single document's peak memory in extract() regardless of its
        # total page count. Only meaningful on GPU (config.py leaves it
        # None on CPU, ample system RAM); explicitly None here disables
        # chunking (single predict() call, the original behavior).
        self._max_pages_per_call = max_pages_per_call

    def supported_formats(self) -> set[str]:
        return {"application/pdf", "image/png", "image/jpeg"}

    def accepts(self, filename: str, content_type: str) -> bool:
        return content_type in self.supported_formats()

    def warmup(self) -> None:
        """Run one tiny synthetic document through the pipeline so the
        first real request doesn't also pay one-time model-load latency."""
        try:
            self.extract(b"%PDF-1.4\n%%EOF", "_warmup.pdf")
        except Exception:
            pass  # best-effort — a real request pays this cost otherwise

    async def aclose(self) -> None:
        pass  # no held resources beyond the in-process paddle pipeline

    async def aextract(self, data: bytes, filename: str) -> ExtractionResult:
        import asyncio

        return await asyncio.to_thread(self.extract, data, filename)

    async def aextract_batch(
        self, items: list[tuple[bytes, str]]
    ) -> list[ExtractionResult]:
        import asyncio

        return await asyncio.to_thread(self.extract_batch, items)

    def extract(self, data: bytes, filename: str) -> ExtractionResult:
        """Run layout+chart detection + OCR over every page of *data*."""
        suffix = Path(filename).suffix or ".pdf"
        if self._max_pages_per_call is not None and suffix.lower() == ".pdf":
            page_count = _pdf_page_count(data)
            if page_count is not None and page_count > self._max_pages_per_call:
                return self._extract_pdf_in_chunks(data, page_count)
        with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
            tmp.write(data)
            tmp.flush()
            results = list(self._pipeline.predict(tmp.name))
        pages, markdown_pages = self._pages_from_results(results)
        return ExtractionResult(
            pages=pages,
            markdown=self._finalize_markdown(pages, markdown_pages),
            engine=self.name,
        )

    def _extract_pdf_in_chunks(self, data: bytes, page_count: int) -> ExtractionResult:
        """Split a PDF wider than ``max_pages_per_call`` into page-range
        chunks and run one ``predict()`` call per chunk instead of one for
        the whole document, bounding peak memory to a single chunk's
        worth of pages regardless of the document's total length. Page
        numbers and markdown paragraph-continuation are made identical to
        a single predict() call would have produced — chunking is an
        internal memory-management detail, not something a caller should
        be able to observe in the result shape.
        """
        import pypdfium2 as pdfium

        chunk_size = self._max_pages_per_call
        assert chunk_size is not None  # only called when set
        pages: list[ExtractedPage] = []
        markdown_pages: list[dict[str, Any]] = []
        src = pdfium.PdfDocument(data)
        try:
            for start in range(0, page_count, chunk_size):
                end = min(start + chunk_size, page_count)
                chunk_doc = pdfium.PdfDocument.new()
                try:
                    chunk_doc.import_pages(src, list(range(start, end)))
                    buf = io.BytesIO()
                    chunk_doc.save(buf)
                finally:
                    chunk_doc.close()
                with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
                    tmp.write(buf.getvalue())
                    tmp.flush()
                    results = list(self._pipeline.predict(tmp.name))
                chunk_pages, chunk_markdown_pages = self._pages_from_results(
                    results, page_offset=start
                )
                pages.extend(chunk_pages)
                markdown_pages.extend(chunk_markdown_pages)
        finally:
            src.close()
        return ExtractionResult(
            pages=pages,
            markdown=self._finalize_markdown(pages, markdown_pages),
            engine=self.name,
        )

    def extract_batch(self, items: list[tuple[bytes, str]]) -> list[ExtractionResult]:
        """Extract multiple documents in ONE ``predict()`` call.

        Real, found-not-assumed reason this matters: ``predict()`` accepts
        ``list[str]`` (paddlex's own signature — it renders each path's
        pages internally, PDFs included, via pypdfium2) and each yielded
        page result carries its own ``input_path``
        (paddlex/inference/pipelines/layout_parsing/pipeline_v2.py — set
        alongside ``page_index``), so results are demuxable back to their
        source file with no extra bookkeeping. A single document's pages
        often don't have enough text regions to fill a large
        ``ocr_batch_size`` on their own (a sparse page might have 3-4); a
        multi-file predict() call lets paddlex's batch sampler group OCR/
        layout inference across ALL these files' pages together instead,
        which is real, additional GPU-batching headroom this pipeline was
        leaving on the table before.
        """
        if not items:
            return []
        with contextlib.ExitStack() as stack:
            tmp_paths: list[str] = []
            for data, filename in items:
                suffix = Path(filename).suffix or ".pdf"
                tmp = stack.enter_context(tempfile.NamedTemporaryFile(suffix=suffix))
                tmp.write(data)
                tmp.flush()
                tmp_paths.append(tmp.name)

            results = list(self._pipeline.predict(tmp_paths))

        by_path: dict[str, list[Any]] = {p: [] for p in tmp_paths}
        for res in results:
            by_path[res.get("input_path")].append(res)

        out: list[ExtractionResult] = []
        for p in tmp_paths:
            pages, markdown_pages = self._pages_from_results(by_path[p])
            out.append(
                ExtractionResult(
                    pages=pages,
                    markdown=self._finalize_markdown(pages, markdown_pages),
                    engine=self.name,
                )
            )
        return out

    def _pages_from_results(
        self, results: Iterable[Any], *, page_offset: int = 0
    ) -> tuple[list[ExtractedPage], list[dict[str, Any]]]:
        return _pages_from_results_for_pipeline(results, page_offset=page_offset)

    def _finalize_markdown(
        self, pages: list[ExtractedPage], markdown_pages: list[dict[str, Any]]
    ) -> str:
        return _finalize_markdown_for_pipeline(self._pipeline, pages, markdown_pages)


def _score_lookup(
    layout_det_res: Any,
) -> list[tuple[tuple[float, float, float, float], float]]:
    """``[(bbox, score), ...]`` from the raw detection result, for nearest-
    bbox matching against ``parsing_res_list`` blocks (see ``extract()``)."""
    if layout_det_res is None:
        return []
    boxes = layout_det_res.get("boxes") if hasattr(layout_det_res, "get") else None
    if not boxes:
        return []
    out = []
    for box in boxes:
        coord = box.get("coordinate")
        score = box.get("score")
        if coord is not None and score is not None:
            out.append((tuple(float(c) for c in coord), float(score)))
    return out


def _nearest_score(
    lookup: list[tuple[tuple[float, float, float, float], float]],
    bbox: Any,
) -> float:
    """Best-effort confidence lookup — defaults to 0.0 (never raises) since
    a missing score must never fail extraction."""
    if not lookup or bbox is None or len(bbox) != 4:
        return 0.0
    target = tuple(float(c) for c in bbox)
    best_score = 0.0
    best_dist = float("inf")
    for coord, score in lookup:
        dist = sum((a - b) ** 2 for a, b in zip(coord, target))
        if dist < best_dist:
            best_dist = dist
            best_score = score
    return best_score


__all__ = ["PaddleClassicEngine"]
