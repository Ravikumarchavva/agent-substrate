"""Chart-quality helpers for the excel_report skill.

Import this instead of retyping the same functions from SKILL.md by hand —
that's how a real bug shipped: a freshly-retyped chart, one run out of three,
ended up with openpyxl's default `axPos="l"` on BOTH the category and value
axis for a horizontal bar chart (openpyxl does not auto-adjust axis position
for `chart.type = "bar"`), producing overlapping axis labels with no visible
bars. Calling these functions instead of reproducing their logic makes that
class of bug structurally impossible — the code only exists in one place.

Usage inside code_interpreter:

    import sys
    sys.path.insert(0, "scripts")
    from substrate_excel_charts import (
        style_chart, axis_number_format, assert_chart_scale_compatible,
        CHART_ACCENT, CHART_PALETTE,
    )
"""

from __future__ import annotations

from openpyxl.chart import BarChart
from openpyxl.chart.axis import ChartLines
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.drawing.line import LineProperties

# Matches this product's own accent color (--border-accent: #2563eb in
# ravi/src/app/globals.css) so a generated report reads as part of this
# product, not generic Excel output.
CHART_ACCENT = "2563EB"
CHART_PALETTE = ["2563EB", "64748B", "0EA5A4", "F59E0B", "9333EA"]


def assert_chart_scale_compatible(
    values: list[float], *, max_ratio: float = 30
) -> None:
    """Raise if `values` span more than `max_ratio`x in magnitude.

    Mixing e.g. Diluted EPS (~$2) with Total net sales (~$120,000) on one
    linear axis makes the small series collapse to a zero-height sliver —
    and because there's no real bar geometry left to anchor a label to, the
    category labels for EVERY series in the chart pile up and overlap, not
    just the small one. Catching this before wb.save() means a stakeholder
    never opens a chart that's already broken.
    """
    nonzero = [abs(v) for v in values if v]
    if not nonzero:
        return
    ratio = max(nonzero) / min(nonzero)
    if ratio > max_ratio:
        raise ValueError(
            f"Chart series spans {ratio:.0f}x in magnitude "
            f"({min(nonzero)}..{max(nonzero)}) — these values don't belong "
            "on one axis. Split into separate charts, or add the smaller-"
            "scale series on a secondary axis instead."
        )


def axis_number_format(
    max_abs_value: float, *, already_millions: bool
) -> tuple[str, str]:
    """Pick an axis number format + title that actually renders correctly —
    don't hardcode a comma-scaled format without checking the data's real
    scale first. Excel's trailing-comma trick divides by 1,000 per comma;
    applying it to a value that's ALREADY expressed in millions (which is how
    financial statements report figures — e.g. Apple's own 10-Q states
    "In millions", so a cell value of 119575 already means $119,575 million,
    not $119,575 raw dollars) silently divides by another 1,000,000 and
    collapses every axis tick to "$0M". Verify this by actually opening the
    rendered file and reading the axis — checking the saved chart XML for a
    solidFill or numFmt string being present is NOT the same as confirming
    the number it produces is sane.
    """
    if already_millions:
        if max_abs_value >= 1000:  # >= $1B — abbreviate further
            return '$#,##0.0,"B"', "USD (billions)"
        return '$#,##0"M"', "USD (millions)"
    # Raw dollar values (not pre-scaled) — one comma per 1,000x is correct here.
    if max_abs_value >= 1_000_000:
        return '$#,##0,,"M"', "USD (millions)"
    if (
        max_abs_value < 10
    ):  # per-share-style values need decimals, not rounding to whole dollars
        return "$#,##0.00", "USD"
    return "$#,##0", "USD"


def style_chart(chart: BarChart, *, single_series: bool = True) -> None:
    """Replace openpyxl's default blue/orange/gray look with a clean,
    branded style — no gray plot-area background, no default gridlines, no
    redundant legend on a single-series chart, and correct axis positions for
    a horizontal bar chart. Call this on every chart right before
    ws.add_chart(), after `chart.type = "bar"` is set.
    """
    # openpyxl's axPos defaults to "l" (left) on BOTH the category and value
    # axis no matter what chart.type is set to — it does NOT auto-adjust for
    # a horizontal bar chart. Some renderers tolerate the resulting mismatch
    # and infer the right layout anyway; others don't, rendering both axes'
    # labels stacked on top of each other with no visible bars.
    if chart.type == "bar":  # horizontal — category axis left, value axis bottom
        chart.x_axis.axPos = "l"
        chart.y_axis.axPos = "b"
    chart.plot_area.graphicalProperties = GraphicalProperties(noFill=True)
    chart.y_axis.majorGridlines = ChartLines(
        spPr=GraphicalProperties(ln=LineProperties(solidFill="E5E7EB"))
    )
    chart.x_axis.majorGridlines = None
    for i, series in enumerate(chart.series):
        series.graphicalProperties = GraphicalProperties(
            solidFill=CHART_ACCENT
            if single_series
            else CHART_PALETTE[i % len(CHART_PALETTE)]
        )
    if single_series:
        chart.legend = None
