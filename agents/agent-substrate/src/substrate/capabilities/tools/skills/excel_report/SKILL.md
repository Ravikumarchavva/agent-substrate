---
name: excel-report
description: Build a professional, stakeholder-ready Excel workbook (formatted tables, native charts, correct per-row citations) instead of a bare data dump.
version: "1.0"
license: MIT
allowed-tools: code_interpreter knowledge_search
category: reporting
tags: [excel, xlsx, report, workbook, spreadsheet, stakeholder, chart, openpyxl]
aliases: [excel-workbook, xlsx-report, stakeholder-report]
metadata:
  author: agent-framework
---

# Excel Report Skill

Use this skill whenever the user asks for an Excel file, workbook, spreadsheet,
or "report" they intend to show someone else — a stakeholder, a manager, a
client. That last part matters: a report someone else will read has a much
higher bar than a CSV dump. If they just want a quick data export, plain
`pandas.DataFrame.to_excel(...)` is enough and this skill is overkill.

## What a bad Excel report looks like (avoid this)

The single most common failure is producing a workbook that is technically an
`.xlsx` file but conveys nothing more than a chat answer would have:

- **Every row cites the same source number** — e.g. `[1]` next to "Apple
  Inc.", `[1]` next to "Q1 FY24", `[1]` next to "In millions" — because the
  citation number from the *first* fact retrieved got pasted onto every
  subsequent row instead of being looked up per-fact. This is actively
  misleading, not just sloppy: it tells the reader page 2 supports a claim
  that actually came from page 1.
- **Restating metadata instead of extracting the actual numbers.** "Amount:
  Apple Inc." is not a number — it's the company name copied into a column
  labeled "Amount." If the user asked for a financial report, the sheet needs
  the real figures (revenue, net income, total assets — whatever the source
  actually contains), not a re-summary of what the document is *about*.
  Before writing a single cell, look at what you actually retrieved
  (`knowledge_search` results, or the extracted document text) and confirm
  you have real numeric values for every row you're about to write. If you
  don't, go get them — issue more targeted searches — rather than filling the
  row with whatever text is closest at hand.
- **A wall of unstyled cells.** Default black-on-white, no header
  distinction, uneven column widths, raw floats like `39895.0` instead of
  `$39,895`. This is what happens when the workbook is built with a single
  `pd.DataFrame(...).to_excel(path)` call and nothing else.
- **No charts**, even when the data is obviously chart-shaped (a trend across
  periods, a breakdown across categories) and the user asked for something
  presentable. A stakeholder report summarizing financials without a single
  chart is presenting the least accessible version of the data possible.
- **Mixing incompatible scales on one chart.** Plotting `Diluted EPS` (~$2)
  on the same linear axis as `Total net sales` (~$120,000) makes the EPS bar
  collapse to a sliver at zero height — and, because there's no real bar
  geometry left for the chart to anchor a label to, the category labels for
  *every* series pile up and overlap into unreadable garbage, not just the
  one bad series. **Don't rely on remembering this while writing chart code**
  — Step 2 below gives you a guard function that raises before a bad chart
  gets built, and Step 5 re-checks the saved file independently. Use both;
  a rule you have to recall correctly every time you write ad-hoc plotting
  code is exactly how this kind of bug gets through.

## Workflow

### Step 1 — Gather real data, with correct per-fact provenance

Before opening a spreadsheet at all, know exactly which fact came from which
source. If you're pulling from a document, run targeted `knowledge_search`
calls per section/topic and keep track of which citation index backs which
specific number — a dict keyed by fact, not one citation reused globally, e.g.:

```python
facts = [
    {"item": "Total net sales", "value": 119575, "source": "[1] p.2"},
    {"item": "Net income", "value": 33916, "source": "[2] p.1"},
    {"item": "Total assets", "value": 353514, "source": "[3] p.2"},
]
```

If two facts genuinely come from the same page, they legitimately share a
citation — that's fine. What's not fine is every row sharing one citation by
default because nothing tracked where each number actually came from.

### Step 2 — Build the workbook with `openpyxl`, not a bare DataFrame dump

`openpyxl` is installed in the sandbox, alongside `pandas`. Use it directly
for anything the user will actually look at — it gives you real control over
formatting and native (editable, not a pasted image) charts.

**Import `style_chart`/`axis_number_format`/`assert_chart_scale_compatible`
from `scripts/substrate_excel_charts.py` — do NOT retype these from memory.**
Activating this skill already staged that file into your sandbox session at
`scripts/substrate_excel_charts.py`. Retyping the logic yourself is exactly
how a real bug shipped: a freshly-retyped chart, on one run out of three,
ended up with mismatched axis positions that a slightly different renderer
tolerated on some charts and not others — inconsistent because it was
regenerated code each time, not a guaranteed function call. Importing the
real function makes that whole class of bug structurally impossible:

```python
import sys
sys.path.insert(0, "scripts")
from substrate_excel_charts import (
    style_chart, axis_number_format, assert_chart_scale_compatible,
    CHART_ACCENT, CHART_PALETTE,
)
```

If that import fails for any reason (e.g. activation happened in an older
session before this existed), fall back to pasting the four functions'
source directly from `scripts/substrate_excel_charts.py` — do not silently
skip styling/the scale guard.

```python
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.chart import BarChart, Reference
from openpyxl.utils import get_column_letter

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

# Currency formatting on the value column, not raw floats.
for cell in ws["B"][1:]:
    cell.number_format = '#,##0'

# Column widths sized to content, not Excel's cramped default.
widths = {"A": 28, "B": 16, "C": 14}
for col, width in widths.items():
    ws.column_dimensions[col].width = width

# Freeze the header row so it stays visible while scrolling.
ws.freeze_panes = "A2"

# Group facts by scale BEFORE charting — this is what actually prevents the
# EPS-vs-revenue bug, not a comment reminding you not to do it. Real reports
# routinely mix a few per-share/percentage metrics into a table of dollar
# figures; that's fine for the TABLE, it just means they need separate charts.
dollar_facts = [f for f in facts if f["item"] not in {"Diluted EPS"}]
ratio_facts = [f for f in facts if f["item"] in {"Diluted EPS"}]

assert_chart_scale_compatible([f["value"] for f in dollar_facts])
chart = BarChart()
chart.type = "bar"  # horizontal — category labels read normally along the
# y-axis instead of rotating/crowding along the x-axis, which is what the
# sizing formula below ("~1.5cm of height per category") actually assumes.
chart.title = "Key Figures (USD millions)"
# Abbreviated, correctly-scaled axis labels ($120M, not 120000) — a raw
# number forces the axis to reserve width for the longest one, which starves
# the plot area and crowds the category labels next to it. Facts here are
# already expressed in millions (matching the source statement's own
# convention), so already_millions=True — get this flag wrong and every
# axis tick renders as "$0M" (a real bug caught only by actually opening the
# rendered file and reading the axis, not by checking the chart XML exists).
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
# Single series here ("Value") — pass single_series=False instead for a
# multi-series chart (e.g. this-period-vs-last-period bars), which switches
# to CHART_PALETTE and keeps the legend so each series stays distinguishable.
style_chart(chart, single_series=True)
# Explicit size, scaled to how much label text has to fit — openpyxl's
# default (15cm x 7.5cm) is what actually produces the garbled, overlapping
# category labels once there are more than 2-3 of them or any label runs
# long ("Wearables, Home and Accessories"): the plot area has nowhere near
# enough room and axis text collides with itself. There's no reliable
# formula — as a starting point, ~1.5cm of height per category for a
# horizontal bar chart, minimum 10cm; widen further if any label exceeds
# ~20 characters.
longest_label = max(len(f["item"]) for f in dollar_facts)
chart.height = max(10, 1.5 * len(dollar_facts))
chart.width = 20 if longest_label > 20 else 16
ws.add_chart(chart, "E2")

# The excluded per-share/percentage metrics get their own small chart (or, for
# a single value, are just left in the table — a bar chart with one bar adds
# nothing) rather than being silently dropped.
```

### Step 3 — Structure multi-topic reports as multiple sheets, not one wide table

`wb.create_sheet("Balance Sheet")`, one sheet per statement/topic/section —
mirrors how the source document itself is organized, and lets each sheet have
its own appropriately-sized columns and its own chart rather than fighting
for space in one giant table. A short "Summary" or "Overview" sheet first,
with the detail sheets after, reads far better than one flat dump.

### Step 4 — A real Sources sheet, if citations matter

If the report cites multiple documents/pages, add one `Sources` sheet mapping
each citation number to its full reference (filename + page) — written out
in full, not just repeating the bracketed number:

```python
sources_ws = wb.create_sheet("Sources")
sources_ws.append(["Ref", "Source"])
for i, (filename, page) in enumerate(unique_sources, start=1):
    sources_ws.append([f"[{i}]", f"{filename}, p.{page}"])

wb.save("stakeholder_report.xlsx")
```

### Step 5 — Verify the *saved file*, not just the code that wrote it

The guard in Step 2 only catches a bad chart if you remembered to call it —
if you wrote a second chart later in the script (a different sheet, a
follow-up edit) and didn't route it through the same check, it slips
through silently. Close that gap by re-opening the file you just saved and
re-running the same check independently, against the real saved chart data
rather than the in-memory values you think you wrote:

```python
from openpyxl import load_workbook

check_wb = load_workbook("stakeholder_report.xlsx")
problems = []
for sheet in check_wb.worksheets:
    for chart in getattr(sheet, "_charts", []):
        for series in chart.series:
            ref = series.val.numRef
            if ref is None:
                continue
            cells = sheet[ref.f.split("!")[-1].replace("$", "")]
            values = [c.value for row in cells for c in row if isinstance(c.value, (int, float))]
            try:
                assert_chart_scale_compatible(values)
            except ValueError as exc:
                problems.append(f"{sheet.title}: {exc}")

if problems:
    raise RuntimeError("Fix these before presenting the file:\n" + "\n".join(problems))
print("Workbook verified — no scale-incompatible charts.")
```

If this raises, fix the flagged chart and re-save — do not present a file
that failed its own verification, and do not weaken `max_ratio` just to make
the check pass.

### Step 6 — Present it

Reference the saved file in your final answer as a `sandbox:` link so the
frontend surfaces it as a downloadable artifact (see the code interpreter
tool's own presentation instructions) — e.g.
`[Download stakeholder_report.xlsx](sandbox:stakeholder_report.xlsx)`. Briefly
describe what's in it (sheet names, what the chart shows) rather than
re-pasting the whole table into the chat — the workbook is the deliverable.

## Checklist before calling it done

- [ ] Every value cell holds a real extracted number/fact, not restated metadata
- [ ] Every citation is the number that actually backs *that specific* row
- [ ] Header row is visually distinct (fill + bold) and frozen
- [ ] Numeric columns use an appropriate number format, not raw floats
- [ ] Column widths fit their content
- [ ] At least one native chart if the data has any trend/comparison/breakdown shape
- [ ] Every chart's series passed `assert_chart_scale_compatible` at build time
- [ ] Every chart called `style_chart()` — not openpyxl's default blue/orange/gray theme with a gray plot background
- [ ] Step 5's independent re-check of the *saved file* ran clean (no bypassing it)
- [ ] Multi-topic content is split across sheets, not crammed into one table
