---
name: summarization
description: Summarization skill for condensing long texts, documents, and conversations into clear, structured summaries.
version: "1.0"
license: MIT
allowed-tools: document_analyzer
category: creative
tags: [summarize, condense, tldr, digest, brief, abstract, recap, shorten]
aliases: [summarize-text, tldr, make-summary]
metadata:
  author: agent-framework
---

# Summarization Skill

Use this skill when the user asks to summarize, condense, or create a digest of text, documents, or conversations.

## Summarization Procedure

### Step 1 — Assess the Input
- What type of content? (article, documentation, conversation, report, code)
- How long is it? (short: <500 words, medium: 500-3000, long: 3000+)
- What is the user's purpose? (quick overview, decision-making, sharing)

### Step 2 — Choose Summary Style
Based on context, select:
- **Executive summary** — 3-5 sentences for decision-makers
- **Bullet points** — key facts and takeaways
- **Structured** — sections with headers (for long documents)
- **TL;DR** — 1-2 sentence ultra-brief
- **Progressive** — one-liner → paragraph → full summary

### Step 3 — Extract Key Information
Identify and preserve:
- Main argument or thesis
- Key facts, numbers, and dates
- Important names and entities
- Conclusions and recommendations
- Action items (if applicable)

### Step 4 — Compose
- Lead with the most important information.
- Maintain the original tone (formal, casual, technical).
- Preserve technical accuracy — never misrepresent data.
- Keep the summary proportional (10-20% of original length).

### Step 5 — Verify
- Does the summary capture ALL key points?
- Is anything important missing?
- Is the summary standalone (understandable without the original)?
- Are there any misleading simplifications?

## Output Format
```
## Summary
[Executive summary paragraph]

## Key Points
- Point 1
- Point 2
- Point 3

## Details
[Only if requested or content is complex]
```

## Rules
- Never add information not in the original.
- Never inject personal opinions into factual summaries.
- Always note if the original contained conflicting claims.
- For technical content, preserve precise terminology.
