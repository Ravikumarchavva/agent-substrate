"""XYCutPDFExtractor — real pdfplumber+reportlab round trip, no mocks.

The point of this module is fixing multi-column reading order (verified
interactively this session on a real 2-column brochure, outside this repo)
and fixing a real upstream paddlex crash at word-level granularity — both
covered here with a genuine synthetic 2-column PDF built via reportlab
(already a repo dependency), not a mock."""

from __future__ import annotations

import io

import pytest

pytest.importorskip("pdfplumber")
pytest.importorskip("reportlab")

from substrate.capabilities.knowledge.loaders.xycut_extractor import (
    XYCutPDFExtractor,
)


def _two_column_pdf() -> bytes:
    """A real PDF with two side-by-side text columns, built with reportlab.
    Column 1 (left) and column 2 (right) each have 3 lines, at the same
    y-coordinates — the exact shape that breaks pdfplumber's own
    extract_text()/extract_text_lines() (they read left-to-right across
    the whole page width at each y-level, ignoring column boundaries)."""
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(400, 300))
    left_lines = ["AlphaOne", "AlphaTwo", "AlphaThree"]
    right_lines = ["BetaOne", "BetaTwo", "BetaThree"]
    y = 250
    for left, right in zip(left_lines, right_lines):
        c.drawString(30, y, left)
        c.drawString(220, y, right)
        y -= 30
    c.showPage()
    c.save()
    return buf.getvalue()


async def test_extract_preserves_column_reading_order_not_row_order():
    """The real bug this module fixes: a naive left-to-right-at-each-y read
    would interleave 'AlphaOne BetaOne AlphaTwo BetaTwo...' — the fixed
    XY-cut must instead read all of column 1 before column 2."""
    data = _two_column_pdf()
    extractor = XYCutPDFExtractor()

    result = await extractor.extract(data, "two_column.pdf")

    assert result.success is True
    assert len(result.pages) == 1
    text = result.pages[0].text

    alpha_positions = [text.index(w) for w in ("AlphaOne", "AlphaTwo", "AlphaThree")]
    beta_positions = [text.index(w) for w in ("BetaOne", "BetaTwo", "BetaThree")]
    # All of column 1 (Alpha) must appear before all of column 2 (Beta) —
    # not interleaved by row.
    assert max(alpha_positions) < min(beta_positions)


async def test_extract_no_images_is_a_documented_gap_not_a_crash():
    data = _two_column_pdf()
    extractor = XYCutPDFExtractor()

    result = await extractor.extract(data, "two_column.pdf")

    assert result.pages[0].images == []


async def test_extract_handles_word_level_granularity_without_the_upstream_crash():
    """Regression test for the real bug found this session: paddlex's own
    recursive_xy_cut crashes with ValueError('zero-size array to reduction
    operation minimum') on an empty x-interval chunk at word-level
    granularity (finer than the line-level boxes PPStructureV3 itself
    feeds it). This many independent words on one page is exactly what
    triggered it — it must not raise."""
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(600, 800))
    y = 780
    for i in range(80):
        c.drawString(30 + (i % 4) * 140, y, f"word{i}")
        if i % 4 == 3:
            y -= 15
    c.showPage()
    c.save()
    data = buf.getvalue()

    extractor = XYCutPDFExtractor()
    result = await extractor.extract(data, "dense.pdf")  # must not raise

    assert result.success is True
    assert "word0" in result.pages[0].text


async def test_extract_empty_page_returns_empty_text_not_error():
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(400, 300))
    c.showPage()
    c.save()

    extractor = XYCutPDFExtractor()
    result = await extractor.extract(buf.getvalue(), "blank.pdf")

    assert result.success is True
    assert result.pages[0].text == ""
