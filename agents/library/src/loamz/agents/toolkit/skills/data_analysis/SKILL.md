---
name: data-analysis
description: Data analysis workflow skill for exploring, cleaning, and deriving insights from tabular and structured data.
version: "1.0"
license: MIT
allowed-tools: code_interpreter data_visualizer file_manager data_catalog
metadata:
  author: agent-framework
---

# Data Analysis Skill

Use this skill when the user provides a dataset (CSV, JSON, etc.) or asks to analyze, explore, or visualize data.

## Warehouse data (when `data_catalog` is available)

If the user asks about data that lives in the company warehouse rather than a file they gave you, use
governed data instead of asking for an export:

1. **Find it.** `data_catalog` with `action="search"` (a word from the question) lists the tables and views
   you may read; `action="describe"` shows columns, partitioning, row count and sample rows. If nothing
   matches, say so; never guess table names. You only see what the current user may read.
2. **Ask SQL for the answer.** In `code_interpreter`:
   ```python
   import loamz.data.client as sd
   df = sd.sql("SELECT date_trunc('month', ts) AS month, sum(amount) AS revenue "
               "FROM main.sales.orders WHERE ts >= '2026-01-01' GROUP BY 1 ORDER BY 1")   # polars.DataFrame
   ```
   Filter, group and limit **in SQL**; the warehouse is built for it and pulling raw rows into Python is slow
   and refused when large. Filter on the table's partition column (shown by `describe`) so only the
   needed files are read. Read-only; there is no way to change warehouse data from here.
3. **Analyse and present.** Continue in Polars (`df.with_columns(...)`), draw charts with matplotlib
   (`plt.savefig("chart.png")`) and write spreadsheets with `df.write_excel("report.xlsx")`, then link them
   with `sandbox:`. Each run is a fresh interpreter, so re-query rather than relying on variables.
4. **Be honest about access.** A `404`/"not found" on a table means you may not read it (or it does not
   exist); do not retry with other names to get around it. Report what you could and could not reach.

## Analysis Workflow

### Step 1 — Load & Inspect
- Load the data using `code_interpreter` (pandas preferred).
- Show: shape, columns, dtypes, first 5 rows.
- Check for: missing values, duplicates, data type mismatches.

### Step 2 — Clean
- Handle missing values (drop, fill, interpolate — ask user if ambiguous).
- Fix data types (dates as datetime, numbers as numeric).
- Remove duplicates if appropriate.
- Note any cleaning decisions made.

### Step 3 — Explore
- Compute summary statistics (mean, median, std, min, max).
- Identify distributions (normal, skewed, categorical).
- Look for outliers (IQR method or z-score).
- Check correlations between numeric columns.

### Step 4 — Visualize
Use `data_visualizer` or `code_interpreter` to create:
- **Distribution**: histograms or box plots for key columns
- **Relationships**: scatter plots or correlation heatmaps
- **Trends**: line charts for time-series data
- **Categories**: bar charts for categorical breakdowns

### Step 5 — Derive Insights
- What are the key patterns or trends?
- Are there surprising findings or anomalies?
- What segments or groups emerge from the data?
- What are the practical implications?

### Step 6 — Present Findings
Structure your analysis report as:
```
## Dataset Overview
- Source, size, time range

## Key Findings
1. Finding with supporting statistic
2. Finding with supporting statistic

## Visualizations
[charts created above]

## Recommendations
- Actionable suggestions based on findings
```

## Best Practices
- Always show your work — include the code used for analysis.
- State assumptions explicitly.
- Distinguish correlation from causation.
- Round numbers appropriately for readability.
