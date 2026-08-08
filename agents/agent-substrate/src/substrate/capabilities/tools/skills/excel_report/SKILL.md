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
formatting and native (editable, not a pasted image) charts:

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

# A native, editable chart — not a matplotlib PNG pasted in.
chart = BarChart()
chart.title = "Key Figures"
chart.y_axis.title = "USD (millions)"
data = Reference(ws, min_col=2, min_row=1, max_row=len(facts) + 1)
cats = Reference(ws, min_col=1, min_row=2, max_row=len(facts) + 1)
chart.add_data(data, titles_from_data=True)
chart.set_categories(cats)
ws.add_chart(chart, "E2")

wb.save("stakeholder_report.xlsx")
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
```

### Step 5 — Present it

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
- [ ] Multi-topic content is split across sheets, not crammed into one table
