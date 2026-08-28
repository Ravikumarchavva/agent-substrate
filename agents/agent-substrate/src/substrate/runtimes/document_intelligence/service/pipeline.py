"""PaddleOCR-based layout/chart extraction — the engine behind ``/v1/extract``.

Replaces the old Docling+EasyOCR path entirely (see the RAG-pipeline plan in
this repo's history for the benchmark that motivated it): PaddleOCR's layout
model identifies chart/table/figure regions with an explicit ``chart`` label
(97%+ confidence on real financial filings, vs. Docling's generic ``picture``
label) and is ~8x faster per page than Docling's layout pass.

All shapes here are verified against a real `paddleocr==3.7.0` /
`paddlex==3.7.2` install — not from documentation. In particular:
``PPStructureV3.predict()`` yields one dict per page with a
``parsing_res_list`` of ``LayoutBlock`` objects (``.label``, ``.content``,
and — only for chart/table blocks — ``.image["img"]``, a real cropped
``PIL.Image``, no manual bbox math needed).
"""

from __future__ import annotations

import io
import re
import tempfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

# Labels PaddleOCR's layout model can produce that we treat as extractable
# images rather than OCR'd text — a chart/table crop is more useful to a
# vision-capable agent than reflowed OCR text of its contents. "image" was
# a real, found-not-assumed gap: a plain photo/logo/decorative graphic (no
# chart/table structure) gets labeled "image", not "figure" — verified
# against a real 2-page marketing brochure where 3 such blocks (a photo,
# a logo, a branded graphic) were silently falling through to the text
# branch, leaking their garbled OCR'd fragments into the plain-text output
# as if they were real prose, instead of becoming cropped images.
_IMAGE_LABELS = {"chart", "table", "figure", "image"}

# Below this detection confidence, a chart/table/figure region is treated as
# a marginal/spurious call, not extracted as an image. Real numbers from this
# project's own testing: genuine charts/tables on financial filings score
# 0.94-0.97; a single-row invoice line-items table (not a real chart-worthy
# region) scored 0.52 and, extracted anyway, showed up in the product as a
# spuriously-attached "chart" for every unrelated query about that document
# (nothing else in the image store to compete with it). 0.7 sits with a
# comfortable margin below the genuine range and above the observed spurious
# case.
_MIN_IMAGE_CONFIDENCE = 0.7


@dataclass(slots=True)
class ExtractedImage:
    data: bytes
    media_type: str = "image/png"
    page_number: int | None = None
    label: str = "chart"
    confidence: float = 0.0
    # OCR'd text for this block, already computed by the same layout pass
    # (``block.content``) — kept alongside the crop so a confident
    # chart/table is still findable by lexical/exact-text search, not only
    # by visual similarity. ``None`` when OCR produced no text for the block.
    caption: str | None = None
    # Stable id (e.g. "img-p3-0") — the markdown field below references
    # this image via a "cid:{id}" link instead of PaddleX's own
    # filesystem-relative path, which isn't resolvable outside the process.
    id: str = ""


@dataclass(slots=True)
class ExtractedPage:
    page_number: int
    text: str
    images: list[ExtractedImage] = field(default_factory=list)
    # This page's PaddleX-native markdown (real XY-cut reading order, images
    # inline via "cid:{id}" links, tables as HTML) — see extract().
    markdown: str = ""


@dataclass(slots=True)
class ExtractionResult:
    pages: list[ExtractedPage]
    # Whole-document markdown, pages joined via PaddleX's own CJK-aware
    # concatenate_markdown_pages (paragraph continuation across a page
    # break, not a naive "\n\n".join()).
    markdown: str = ""


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


class ExtractionPipeline:
    """One PaddleOCR ``PPStructureV3`` pipeline: layout + chart/table
    detection + OCR, in a single call per document."""

    def __init__(
        self, *, ocr_size: str = "tiny", device: str = "cpu", ocr_batch_size: int = 16
    ) -> None:
        _disable_mkldnn()

        from paddleocr import PPStructureV3

        det_model, rec_model = _OCR_MODELS[ocr_size]
        # PPStructureV3 leaves text_recognition_batch_size/
        # textline_orientation_batch_size at None, which paddlex's
        # BatchSampler defaults to 1 (verified: base_batch_sampler.py's
        # __init__(self, batch_size: int = 1)) — every OCR'd text region on
        # a page (there can be dozens: paragraphs, table cells, ...) gets
        # its own separate inference call. On CPU that's already how the
        # work has to be split up, but on GPU it means dozens of tiny
        # sequential launches instead of one batched one — real, measured
        # as a sawtooth GPU-utilization pattern (repeated short spikes, not
        # sustained load) rather than a bug in this pipeline's own code.
        # ocr_batch_size (config.py's DOCUMENT_INTELLIGENCE_OCR_BATCH_SIZE,
        # default 16) was originally tuned for a 4GB-class laptop GPU — a
        # 24GB+ card has real room to raise it; only ever applied on GPU.
        batch_size = ocr_batch_size if device.startswith("gpu") else None
        self._pipeline = PPStructureV3(
            text_detection_model_name=det_model,
            text_recognition_model_name=rec_model,
            device=device,
            text_recognition_batch_size=batch_size,
            textline_orientation_batch_size=batch_size,
            # Table structure recognition (SLANet, bundled in PP-StructureV3)
            # gives real row/column HTML for table blocks — converted to a
            # markdown table below — instead of just an OCR'd caption. The
            # rest stay off: no formula/seal/chart sub-models needed for the
            # chart/table-image RAG use case this pipeline is built for.
            use_table_recognition=True,
            use_formula_recognition=False,
            use_seal_recognition=False,
            use_chart_recognition=False,
        )

    def warmup(self) -> None:
        """Run one tiny synthetic document through the pipeline so the
        first real request doesn't also pay one-time model-load latency."""
        try:
            self.extract(b"%PDF-1.4\n%%EOF", "_warmup.pdf")
        except Exception:
            pass  # best-effort — a real request pays this cost otherwise

    def extract(self, data: bytes, filename: str) -> ExtractionResult:
        """Run layout+chart detection + OCR over every page of *data*."""
        suffix = Path(filename).suffix or ".pdf"
        with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
            tmp.write(data)
            tmp.flush()
            results = list(self._pipeline.predict(tmp.name))

        pages: list[ExtractedPage] = []
        markdown_pages: list[dict[str, Any]] = []
        for res in results:
            page_no = int(res.get("page_index") or 0) + 1
            # ``parsing_res_list`` (reading-order blocks, has .image crops)
            # and ``layout_det_res.boxes`` (raw detections, has .score) come
            # from the same detection pass but are NOT index-aligned — match
            # by nearest bbox to recover a confidence score for each block.
            score_by_bbox = _score_lookup(res.get("layout_det_res"))

            text_parts: list[str] = []
            images: list[ExtractedImage] = []
            kept_image_paths: dict[str, str] = {}
            dropped_image_paths: set[str] = set()
            for block in res.get("parsing_res_list") or []:
                label = getattr(block, "label", "") or ""
                confidence = _nearest_score(score_by_bbox, getattr(block, "bbox", None))
                raw_content = (getattr(block, "content", "") or "").strip()
                # Table blocks now carry real structured HTML in ``content``
                # (use_table_recognition=True) — convert it to a markdown
                # table for the text stream, not just an image-crop caption,
                # so the plain-text output alone has the actual table data.
                md_table = (
                    _html_table_to_markdown(raw_content) if label == "table" else ""
                )
                img_dict = getattr(block, "image", None)
                img_path = img_dict.get("path") if isinstance(img_dict, dict) else None
                pil_img = img_dict.get("img") if isinstance(img_dict, dict) else None

                # One gate drives both the images[] list and the markdown
                # kept/dropped decision — a block only becomes a cropped
                # image AND a cid: markdown reference if it clears all three
                # checks; anything else that carried an image blob (a
                # low-confidence chart/table, or a label outside
                # _IMAGE_LABELS PaddleX still populated block.image for)
                # gets recorded as dropped so its <img> tag never leaks an
                # unresolvable filesystem path into the markdown field.
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
                # Non-image blocks, and image-labeled blocks below the
                # confidence bar, fall through here — a marginal chart/table
                # detection still surfaces whatever text it has (markdown for
                # a table block, raw OCR text otherwise) rather than being
                # silently dropped.
                content = md_table or raw_content
                if content:
                    text_parts.append(content)

            # Second pass over the same (already-computed) parsing_res_list,
            # via PaddleX's own markdown assembly — confirmed cheap (pure
            # string formatting, no re-inference) and safe to call after the
            # loop above (parsing_res_list is a stored list, not a
            # generator, so consuming it once doesn't exhaust it).
            page_md = res.markdown
            page_markdown_text = _rewrite_markdown_images(
                page_md.get("markdown_texts", ""), kept_image_paths, dropped_image_paths
            )
            # Deliberately drop markdown_images (PIL refs) here — the real
            # bytes are already captured in images[]/ExtractedImage.data;
            # retaining a second full-res PIL copy per embedded image for
            # every page of a 60+ page document is unnecessary memory
            # pressure for a value we don't otherwise use.
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

        document_markdown = ""
        if markdown_pages:
            try:
                document_markdown = self._pipeline.concatenate_markdown_pages(
                    markdown_pages
                ).get("markdown_texts", "")
            except Exception:
                document_markdown = "\n\n".join(p.markdown for p in pages)
        return ExtractionResult(pages=pages, markdown=document_markdown)


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


__all__ = ["ExtractedImage", "ExtractedPage", "ExtractionResult", "ExtractionPipeline"]
