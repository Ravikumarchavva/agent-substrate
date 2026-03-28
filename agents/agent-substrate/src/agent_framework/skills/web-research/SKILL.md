---
name: web-research
description: Multi-step web research procedure for answering questions that require gathering and synthesising information from multiple online sources.
version: "1.0"
license: MIT
allowed-tools: web_search web_fetch
metadata:
  author: agent-framework
  category: research
---

# Web Research Skill

Use this skill when the user asks a question that requires up-to-date information, comparisons, or synthesis from multiple sources.

## Research Procedure

### Step 1 – Decompose the Query
- Identify the core question and any sub-questions.
- List 2–4 search terms that cover different angles of the topic.

### Step 2 – Search
- Run `web_search` for each search term (max 3 searches total).
- Collect the top 3 results from each search.

### Step 3 – Fetch & Read
- For each promising result, call `web_fetch` to retrieve the page content.
- Skim for: dates, statistics, named entities, contrasting viewpoints.

### Step 4 – Synthesise
- Merge findings into a coherent answer.
- Resolve contradictions by citing the most recent or authoritative source.
- Do NOT copy-paste large blocks of text from sources.

### Step 5 – Respond
Structure the answer as:
1. **Direct answer** (1–2 sentences)
2. **Supporting detail** (bullet points with source citations)
3. **Sources** (numbered list of URLs)

## Quality Rules
- Always cite sources inline: "According to [Source](url), ..."
- Flag information older than 12 months as potentially outdated.
- If no reliable sources are found, say so clearly rather than guessing.
- Maximum total token usage for web content: 8 000 tokens.
