# excel_report worked example

Assumes the four functions from `functions.md` are already copied into
scope. This shows them applied to a real table + chart — the pattern to
follow, not something to copy verbatim (your actual facts/sheet layout will
differ).

```python
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.chart import BarChart, Reference

wb = Workbook()
ws = wb.active
ws.title = "Summary"

HEADER_FILL = PatternFill(start_color="1F2937", end_color="1F2937", fill_type="solid")
HEADER_FONT = Font(bold=True, color="FFFFFF")
THIN = Side(style="thin", color="D1D5DB")
BORDER = Border(bottom=THIN)

headers = ["Item", "Value", "Source"]
ws.append(headers)
for col, _ in enumerate(headers, start=1):
    cell = ws.cell(row=1, column=col)
    cell.fill = HEADER_FILL
    cell.font = HEADER_FONT
    cell.alignment = Alignment(vertical="center")

for row in facts:
    ws.append([row["item"], row["value"], row["source"]])

for cell in ws["B"][1:]:
    cell.number_format = '#,##0'  # not raw floats

widths = {"A": 28, "B": 16, "C": 14}  # sized to content, not Excel's default
for col, width in widths.items():
    ws.column_dimensions[col].width = width

ws.freeze_panes = "A2"

# Split by scale before charting — per-share/percentage metrics get their own
# chart, not mixed onto the same axis as dollar figures.
dollar_facts = [f for f in facts if f["item"] not in {"Diluted EPS"}]
ratio_facts = [f for f in facts if f["item"] in {"Diluted EPS"}]

assert_chart_scale_compatible([f["value"] for f in dollar_facts])
chart = BarChart()
chart.type = "bar"  # horizontal — labels stay readable, not rotated
chart.title = "Key Figures (USD millions)"
numfmt, axis_title = axis_number_format(
    max(abs(f["value"]) for f in dollar_facts), already_millions=True
)
chart.y_axis.title = axis_title
chart.y_axis.numFmt = numfmt
start_row = 2  # facts start at row 2 (row 1 is the header)
data = Reference(ws, min_col=2, min_row=1,
                  max_row=start_row + len(dollar_facts) - 1)
cats = Reference(ws, min_col=1, min_row=start_row,
                  max_row=start_row + len(dollar_facts) - 1)
chart.add_data(data, titles_from_data=True)
chart.set_categories(cats)
style_chart(chart, single_series=True)  # single_series=False for multi-series
longest_label = max(len(f["item"]) for f in dollar_facts)
chart.height = max(10, 1.5 * len(dollar_facts))  # ~1.5cm/category, min 10cm
chart.width = 20 if longest_label > 20 else 16
ws.add_chart(chart, "E2")

# ratio_facts get their own small chart, or a table row if just one value.
```
