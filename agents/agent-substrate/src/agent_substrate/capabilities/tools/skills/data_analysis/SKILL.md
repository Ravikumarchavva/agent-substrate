---
name: data-analysis
description: Data analysis workflow skill for exploring, cleaning, and deriving insights from tabular and structured data.
version: "1.0"
license: MIT
allowed-tools: code_interpreter data_visualizer file_manager
category: research
tags: [data, analyze, statistics, trend, insight, csv, pandas, explore]
aliases: [analyze-data, data-exploration, data-insights]
metadata:
  author: agent-framework
---

# Data Analysis Skill

Use this skill when the user provides a dataset (CSV, JSON, etc.) or asks to analyze, explore, or visualize data.

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
