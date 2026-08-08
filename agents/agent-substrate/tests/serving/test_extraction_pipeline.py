"""ExtractionPipeline — bbox-matching helpers (pure Python, no paddleocr
import needed) plus a real end-to-end chart-detection test guarded by
``importorskip`` (paddleocr/paddlepaddle are the optional `extraction`
extra, not part of the default install — see pyproject.toml)."""

from __future__ import annotations

from pathlib import Path

import pytest

from substrate.serving.services.extraction.pipeline import (
    _nearest_score,
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


# ── Confidence gating (no real model needed — fakes the paddlex pipeline) ──


class _FakeBlock:
    def __init__(self, label, content="", image=None, bbox=(0.0, 0.0, 1.0, 1.0)):
        self.label = label
        self.content = content
        self.image = image
        self.bbox = bbox


def _fake_page_image():
    from PIL import Image

    return {"img": Image.new("RGB", (4, 4), color="white")}


def _pipeline_with_fake_result(blocks, *, boxes):
    """An ExtractionPipeline whose ``.extract()`` bbox/OCR logic runs for
    real, but whose underlying paddlex ``.predict()`` call is faked — avoids
    constructing a real (heavy, model-loading) PPStructureV3 pipeline just to
    test the confidence-gating branch."""
    from substrate.serving.services.extraction.pipeline import ExtractionPipeline

    pipeline = object.__new__(ExtractionPipeline)
    fake_result = {
        "page_index": 0,
        "layout_det_res": {"boxes": boxes},
        "parsing_res_list": blocks,
    }
    pipeline._pipeline = type(
        "FakePPStructure", (), {"predict": lambda self, path: [fake_result]}
    )()
    return pipeline


def test_extract_keeps_image_above_confidence_threshold(monkeypatch):
    blocks = [
        _FakeBlock("chart", image=_fake_page_image(), bbox=(0.0, 0.0, 10.0, 10.0)),
    ]
    boxes = [{"coordinate": [0.0, 0.0, 10.0, 10.0], "score": 0.97}]
    pipeline = _pipeline_with_fake_result(blocks, boxes=boxes)

    pages = pipeline.extract(b"irrelevant", "doc.pdf")

    assert len(pages[0].images) == 1
    assert pages[0].images[0].label == "chart"
    assert pages[0].images[0].confidence == 0.97


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
    pipeline = _pipeline_with_fake_result(blocks, boxes=boxes)

    pages = pipeline.extract(b"irrelevant", "doc.pdf")

    assert pages[0].images == []
    assert "leftover OCR text" in pages[0].text


# ── Real model integration test ─────────────────────────────────────────────

pytest.importorskip("paddleocr")


@pytest.mark.skipif(
    not _CHART_FIXTURE.exists(),
    reason=f"chart_page.pdf fixture missing at {_CHART_FIXTURE}",
)
def test_extraction_pipeline_detects_chart_in_real_pdf():
    """Real, non-mocked chart detection — verifies the mkldnn workaround and
    the layout-model chart label end to end, not just unit-level plumbing."""
    from substrate.serving.services.extraction.pipeline import ExtractionPipeline

    pipeline = ExtractionPipeline(ocr_size="tiny")
    pages = pipeline.extract(_CHART_FIXTURE.read_bytes(), "chart_page.pdf")

    assert len(pages) >= 1
    all_images = [img for page in pages for img in page.images]
    assert any(img.label == "chart" for img in all_images)
    chart = next(img for img in all_images if img.label == "chart")
    assert chart.confidence > 0.5
    assert len(chart.data) > 0
