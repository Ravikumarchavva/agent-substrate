# excel_report guard functions

Copy these four functions verbatim into your script before writing any
chart code. See `SKILL.md` Step 2 for what each one is for.

```python
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.chart.axis import ChartLines
from openpyxl.drawing.line import LineProperties

CHART_ACCENT = "2563EB"  # matches this product's own accent color
CHART_PALETTE = ["2563EB", "64748B", "0EA5A4", "F59E0B", "9333EA"]

def assert_chart_scale_compatible(values: list[float], *, max_ratio: float = 30) -> None:
    """Raise if `values` span more than `max_ratio`x in magnitude."""
    nonzero = [abs(v) for v in values if v]
    if not nonzero:
        return
    ratio = max(nonzero) / min(nonzero)
    if ratio > max_ratio:
        raise ValueError(
            f"Chart series spans {ratio:.0f}x in magnitude "
            f"({min(nonzero)}..{max(nonzero)}) — split into separate charts."
        )

def axis_number_format(max_abs_value: float, *, already_millions: bool) -> tuple[str, str]:
    """Pick an axis format that won't collapse to "$0M". Excel's trailing-
    comma trick divides by 1,000 per comma — applying it to a value already
    expressed in millions divides it by another 1,000,000."""
    if already_millions:
        if max_abs_value >= 1000:
            return '$#,##0.0,"B"', "USD (billions)"
        return '$#,##0"M"', "USD (millions)"
    if max_abs_value >= 1_000_000:
        return '$#,##0,,"M"', "USD (millions)"
    if max_abs_value < 10:
        return '$#,##0.00', "USD"
    return '$#,##0', "USD"

def style_chart(chart, *, single_series: bool = True) -> None:
    """Branded styling + correct axis positions for a horizontal bar chart.
    openpyxl's axPos defaults to "l" on BOTH axes regardless of chart.type —
    it does NOT auto-adjust for type="bar", which renders with overlapping
    axis labels and no visible bars. Call this on every chart."""
    if chart.type == "bar":
        chart.x_axis.axPos = "l"
        chart.y_axis.axPos = "b"
    chart.plot_area.graphicalProperties = GraphicalProperties(noFill=True)
    chart.y_axis.majorGridlines = ChartLines(
        spPr=GraphicalProperties(ln=LineProperties(solidFill="E5E7EB"))
    )
    chart.x_axis.majorGridlines = None
    for i, series in enumerate(chart.series):
        series.graphicalProperties = GraphicalProperties(
            solidFill=CHART_ACCENT if single_series else CHART_PALETTE[i % len(CHART_PALETTE)]
        )
    if single_series:
        chart.legend = None

def assert_bar_chart_axes_correct(chart) -> None:
    """Re-check axPos on the SAVED file — catches a chart written as fresh
    ad-hoc code that skipped style_chart()."""
    if getattr(chart, "type", None) != "bar":
        return
    x_pos = getattr(getattr(chart, "x_axis", None), "axPos", None)
    y_pos = getattr(getattr(chart, "y_axis", None), "axPos", None)
    if x_pos == y_pos:
        raise ValueError(
            f"Chart has type='bar' but both axes have axPos={x_pos!r} — set "
            'chart.x_axis.axPos = "l"; chart.y_axis.axPos = "b" (or call style_chart()).'
        )
```
