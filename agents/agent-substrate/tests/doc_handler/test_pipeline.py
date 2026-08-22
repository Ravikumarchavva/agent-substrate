"""ExtractionPipeline — bbox-matching helpers (pure Python, no paddleocr
import needed) plus a real end-to-end chart-detection test guarded by
``importorskip`` (paddleocr/paddlepaddle are the optional `doc-handler`
extra, not part of the default install — see pyproject.toml)."""

from __future__ import annotations

from pathlib import Path

import pytest

from substrate.doc_handler.service.pipeline import (
    _nearest_score,
    _rewrite_markdown_images,
    _score_lookup,
)

_CHART_FIXTURE = Path(__file__).parent.parent / "fixtures" / "chart_page.pdf"


class _FakeLayoutDetRes(dict):
    """Mimics the dict-like ``layout_det_res`` object PPStructureV3 returns."""


def test_score_lookup_extracts_coordinate_score_pairs():
    layout_det_res = _FakeLayoutDetRes(
        boxes=[
            {"coordinate": [0.0, 0.0, 10.0, 10.0], "score": 0.97, "label": "chart"},
            {"coordinate": [20.0, 20.0, 30.0, 30.0], "score": 0.5, "label": "text"},
        ]
    )
    result = _score_lookup(layout_det_res)
    assert result == [((0.0, 0.0, 10.0, 10.0), 0.97), ((20.0, 20.0, 30.0, 30.0), 0.5)]


def test_score_lookup_returns_empty_for_none():
    assert _score_lookup(None) == []


def test_score_lookup_returns_empty_when_no_boxes():
    assert _score_lookup(_FakeLayoutDetRes(boxes=None)) == []


def test_score_lookup_skips_incomplete_boxes():
    layout_det_res = _FakeLayoutDetRes(
        boxes=[
            {"coordinate": [0.0, 0.0, 10.0, 10.0], "score": None},
            {"coordinate": None, "score": 0.9},
        ]
    )
    assert _score_lookup(layout_det_res) == []


def test_nearest_score_picks_closest_bbox():
    lookup = [((0.0, 0.0, 10.0, 10.0), 0.97), ((100.0, 100.0, 110.0, 110.0), 0.3)]
    assert _nearest_score(lookup, (1.0, 1.0, 9.0, 9.0)) == 0.97


def test_nearest_score_defaults_to_zero_for_missing_bbox():
    lookup = [((0.0, 0.0, 10.0, 10.0), 0.97)]
    assert _nearest_score(lookup, None) == 0.0


def test_nearest_score_defaults_to_zero_for_malformed_bbox():
    lookup = [((0.0, 0.0, 10.0, 10.0), 0.97)]
    assert _nearest_score(lookup, (1.0, 2.0, 3.0)) == 0.0


def test_nearest_score_defaults_to_zero_for_empty_lookup():
    assert _nearest_score([], (0.0, 0.0, 10.0, 10.0)) == 0.0


# ── _rewrite_markdown_images (pure Python, no paddlex needed) ──────────────


def _wrapped_img(path: str) -> str:
    """Same shape format_image_scaled_by_html actually renders (pretty=True,
    the default) — a centered div wrapping the img tag."""
    return f'<div style="text-align: center;"><img src="{path}" alt="Image" width="80%" /></div>\n'


def test_rewrite_markdown_images_replaces_kept_path_with_cid():
    md = "before\n" + _wrapped_img("imgs/img_in_chart_box_0_0_10_10.jpg") + "after"
    out = _rewrite_markdown_images(
        md, {"imgs/img_in_chart_box_0_0_10_10.jpg": "img-p1-0"}, set()
    )
    assert 'src="cid:img-p1-0"' in out
    assert "imgs/img_in_chart_box_0_0_10_10.jpg" not in out


def test_rewrite_markdown_images_strips_dropped_div_wrapper():
    md = "before\n" + _wrapped_img("imgs/img_in_table_box_0_0_10_10.jpg") + "after"
    out = _rewrite_markdown_images(md, {}, {"imgs/img_in_table_box_0_0_10_10.jpg"})
    assert "img_in_table_box_0_0_10_10.jpg" not in out
    assert "before" in out and "after" in out


def test_rewrite_markdown_images_bare_img_fallback():
    # Not the real wrapper shape (no surrounding <div>) — must still strip
    # via the bare-<img> fallback, not leave an unresolvable path behind.
    md = 'before <img src="imgs/img_in_figure_box_1_2_3_4.jpg"/> after'
    out = _rewrite_markdown_images(md, {}, {"imgs/img_in_figure_box_1_2_3_4.jpg"})
    assert "img_in_figure_box_1_2_3_4.jpg" not in out
    assert "before" in out and "after" in out


def test_rewrite_markdown_images_noop_when_nothing_matches():
    md = "just plain text, no images"
    assert _rewrite_markdown_images(md, {}, set()) == md


# ── Confidence gating (no real model needed — fakes the paddlex pipeline) ──


class _FakeBlock:
    def __init__(self, label, content="", image=None, bbox=(0.0, 0.0, 1.0, 1.0)):
        self.label = label
        self.content = content
        self.image = image
        self.bbox = bbox


_IMG_PATH = "imgs/img_in_chart_box_0_0_10_10.jpg"


def _fake_page_image(path: str = _IMG_PATH):
    from PIL import Image

    return {"path": path, "img": Image.new("RGB", (4, 4), color="white")}


class _FakeResult(dict):
    """Mimics LayoutParsingResultV2 enough for extract()'s two passes: dict
    access for page_index/layout_det_res/parsing_res_list, and a `.markdown`
    property (real code is a property too, not a dict key) for the second."""

    def __init__(self, *, page_index, boxes, blocks, markdown_texts):
        super().__init__(
            page_index=page_index,
            layout_det_res={"boxes": boxes},
            parsing_res_list=blocks,
        )
        self._markdown_texts = markdown_texts

    @property
    def markdown(self):
        return {
            "markdown_texts": self._markdown_texts,
            "page_continuation_flags": (True, True),
        }


def _pipeline_with_fake_result(blocks, *, boxes, markdown_texts=""):
    """An ExtractionPipeline whose ``.extract()`` bbox/OCR logic runs for
    real, but whose underlying paddlex ``.predict()`` call is faked — avoids
    constructing a real (heavy, model-loading) PPStructureV3 pipeline just to
    test the confidence-gating branch."""
    from substrate.doc_handler.service.pipeline import ExtractionPipeline

    pipeline = object.__new__(ExtractionPipeline)
    fake_result = _FakeResult(
        page_index=0, boxes=boxes, blocks=blocks, markdown_texts=markdown_texts
    )
    pipeline._pipeline = type(
        "FakePPStructure",
        (),
        {
            "predict": lambda self, path: [fake_result],
            "concatenate_markdown_pages": lambda self, pages: {
                "markdown_texts": "\n\n".join(p["markdown_texts"] for p in pages)
            },
        },
    )()
    return pipeline


def test_extract_keeps_image_above_confidence_threshold(monkeypatch):
    blocks = [
        _FakeBlock("chart", image=_fake_page_image(), bbox=(0.0, 0.0, 10.0, 10.0)),
    ]
    boxes = [{"coordinate": [0.0, 0.0, 10.0, 10.0], "score": 0.97}]
    pipeline = _pipeline_with_fake_result(
        blocks, boxes=boxes, markdown_texts=_wrapped_img(_IMG_PATH)
    )

    result = pipeline.extract(b"irrelevant", "doc.pdf")
    pages = result.pages

    assert len(pages[0].images) == 1
    assert pages[0].images[0].label == "chart"
    assert pages[0].images[0].confidence == 0.97
    assert pages[0].images[0].id == "img-p1-0"
    # The kept image's src got rewritten to reference that same id.
    assert 'src="cid:img-p1-0"' in pages[0].markdown
    assert "cid:img-p1-0" in result.markdown


def test_extract_drops_image_below_confidence_threshold_but_keeps_its_text():
    blocks = [
        _FakeBlock(
            "table",
            content="leftover OCR text",
            image=_fake_page_image(),
            bbox=(0.0, 0.0, 10.0, 10.0),
        ),
    ]
    boxes = [{"coordinate": [0.0, 0.0, 10.0, 10.0], "score": 0.52}]
    pipeline = _pipeline_with_fake_result(
        blocks,
        boxes=boxes,
        markdown_texts=_wrapped_img(_IMG_PATH) + "leftover OCR text",
    )

    result = pipeline.extract(b"irrelevant", "doc.pdf")
    pages = result.pages

    assert pages[0].images == []
    assert "leftover OCR text" in pages[0].text
    # The dropped image's path never leaks into the markdown field either.
    assert _IMG_PATH not in pages[0].markdown
    assert "leftover OCR text" in pages[0].markdown


# ── Real model integration test ─────────────────────────────────────────────

pytest.importorskip("paddleocr")


@pytest.mark.skipif(
    not _CHART_FIXTURE.exists(),
    reason=f"chart_page.pdf fixture missing at {_CHART_FIXTURE}",
)
def test_extraction_pipeline_detects_chart_in_real_pdf():
    """Real, non-mocked chart detection — verifies the mkldnn workaround and
    the layout-model chart label end to end, not just unit-level plumbing."""
    from substrate.doc_handler.service.pipeline import ExtractionPipeline

    pipeline = ExtractionPipeline(ocr_size="tiny")
    result = pipeline.extract(_CHART_FIXTURE.read_bytes(), "chart_page.pdf")
    pages = result.pages

    assert len(pages) >= 1
    all_images = [img for page in pages for img in page.images]
    assert any(img.label == "chart" for img in all_images)
    chart = next(img for img in all_images if img.label == "chart")
    assert chart.confidence > 0.5
    assert len(chart.data) > 0
    assert chart.id
    # The chart's cid: reference actually made it into the assembled markdown.
    assert f"cid:{chart.id}" in result.markdown
