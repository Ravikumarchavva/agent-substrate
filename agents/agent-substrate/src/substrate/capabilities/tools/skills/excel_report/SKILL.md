---
name: excel-report
description: Build a professional, stakeholder-ready Excel workbook (formatted tables, native charts, correct per-row citations) instead of a bare data dump.
version: "1.0"
license: MIT
allowed-tools: code_interpreter knowledge_search
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

- **Every row cites the same source number** — the citation from the *first*
  fact retrieved pasted onto every row, instead of looked up per-fact.
- **Restating metadata instead of extracting the actual numbers.** Before
  writing a cell, confirm you have real numeric values for every row — if
  you don't, issue more targeted `knowledge_search` calls.
- **A wall of unstyled cells.** No header distinction, raw floats like
  `39895.0` instead of `$39,895`.
- **No charts**, even when the data is obviously chart-shaped.
- **Mixing incompatible scales on one chart.** Plotting `Diluted EPS` (~$2)
  on the same axis as `Total net sales` (~$120,000) collapses the small
  series to nothing and garbles every category label — Step 2's guard
  function and Step 5's saved-file re-check both catch this.

## Workflow

### Step 1 — Gather real data, with correct per-fact provenance

Before opening a spreadsheet at all, know exactly which fact came from which
source. If pulling from a document, run targeted `knowledge_search` calls per
section/topic and track which citation backs which number — a dict keyed by
fact, not one citation reused globally:

```python
facts = [
    {"item": "Total net sales", "value": 119575, "source": "[1] p.2"},
    {"item": "Net income", "value": 33916, "source": "[2] p.1"},
]
```

### Step 2 — Build the workbook with `openpyxl`, not a bare DataFrame dump

`openpyxl` is installed in the sandbox, alongside `pandas`. Call
`skills(action=read_reference, name="excel-report", file="functions.md")`
and copy these four functions **verbatim** into your script before writing
any chart code — they're the actual fix for scale-mixing and axis-position
bugs, not rules to remember while writing fresh plotting code each time:

- `assert_chart_scale_compatible(values)` — raises if a chart's series span
  too wide a magnitude range to share one axis.
- `axis_number_format(max_abs_value, already_millions=...)` — returns a
  `(numFmt, axis_title)` pair that won't collapse to "$0M".
- `style_chart(chart, single_series=...)` — branded colors, no gray plot
  background, and (critically) correct `axPos` on both axes: openpyxl
  defaults BOTH the category and value axis to `axPos="l"` regardless of
  `chart.type`, which renders a horizontal bar chart with overlapping axis
  labels and no visible bars. Call this on every chart.
- `assert_bar_chart_axes_correct(chart)` — Step 5's independent re-check for
  the same axPos bug, for charts that got written as fresh code and skipped
  `style_chart()`.

For these applied to a full worked report (table styling, sizing, the
actual `BarChart` build), read `file="example.md"` the same way.

### Step 3 — Structure multi-topic reports as multiple sheets, not one wide table

`wb.create_sheet("Balance Sheet")`, one sheet per statement/topic — mirrors
how the source document itself is organized. A short "Summary" sheet first,
detail sheets after, reads far better than one flat dump.

### Step 4 — A real Sources sheet, if citations matter

If the report cites multiple documents/pages, add one `Sources` sheet
mapping each citation number to its full reference:

```python
sources_ws = wb.create_sheet("Sources")
sources_ws.append(["Ref", "Source"])
for i, (filename, page) in enumerate(unique_sources, start=1):
    sources_ws.append([f"[{i}]", f"{filename}, p.{page}"])
wb.save("stakeholder_report.xlsx")
```

### Step 5 — Verify the *saved file*, not just the code that wrote it

Re-open the file and re-run both guard functions — scale compatibility and
axis correctness — against every chart actually in it, not just the ones
you remember writing:

```python
from openpyxl import load_workbook

check_wb = load_workbook("stakeholder_report.xlsx")
problems = []
for sheet in check_wb.worksheets:
    for chart in getattr(sheet, "_charts", []):
        try:
            assert_bar_chart_axes_correct(chart)
        except ValueError as exc:
            problems.append(f"{sheet.title}: {exc}")
        for series in getattr(chart, "series", []):
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
```

If this raises, fix the flagged chart and re-save — do not present a file
that failed its own verification.

### Step 6 — Present it

Reference the saved file as a `sandbox:` link so the frontend surfaces it as
a downloadable artifact — briefly describe what's in it rather than
re-pasting the table into the chat; the workbook is the deliverable.

## Checklist before calling it done

- [ ] Every value cell holds a real extracted number, not restated metadata
- [ ] Every citation is the number that actually backs *that specific* row
- [ ] Header row is visually distinct (fill + bold) and frozen
- [ ] Numeric columns use an appropriate number format, not raw floats
- [ ] At least one native chart if the data has any trend/comparison shape
- [ ] Every chart called `style_chart()`
- [ ] Step 5's saved-file re-check ran clean
- [ ] Multi-topic content is split across sheets, not crammed into one table
