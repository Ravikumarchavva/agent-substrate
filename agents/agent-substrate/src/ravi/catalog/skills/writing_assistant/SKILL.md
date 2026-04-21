---
name: writing-assistant
description: Writing improvement skill for editing, refining, and enhancing written content including tone, clarity, and grammar.
version: "1.0"
license: MIT
allowed-tools: []
category: creative
tags: [write, edit, improve, grammar, tone, style, proofread, rewrite]
aliases: [edit-text, improve-writing, proofread]
metadata:
  author: agent-framework
---

# Writing Assistant Skill

Use this skill when the user asks to improve, edit, proofread, or rewrite text.

## Writing Improvement Procedure

### Step 1 — Understand Intent
Ask or infer:
- What is the text for? (email, report, blog, documentation, social media)
- Who is the audience? (colleagues, customers, general public, technical)
- What tone is desired? (formal, casual, persuasive, neutral)
- What specific improvements? (grammar, clarity, brevity, tone)

### Step 2 — Analyze the Original
Evaluate:
- **Clarity** — Is the meaning immediately clear?
- **Conciseness** — Are there unnecessary words or repetition?
- **Grammar** — Spelling, punctuation, subject-verb agreement
- **Structure** — Logical flow, paragraph breaks, transitions
- **Tone** — Consistent and appropriate for audience?
- **Active voice** — Prefer active over passive where possible

### Step 3 — Edit
Apply improvements in this priority order:
1. **Factual accuracy** — Never change the meaning
2. **Clarity** — Restructure confusing sentences
3. **Conciseness** — Remove filler words ("very", "really", "just", "that")
4. **Grammar** — Fix errors
5. **Tone** — Adjust to match intent
6. **Polish** — Word choice, rhythm, transitions

### Step 4 — Present Changes
Show the improved version, then explain key changes:
```
## Revised Text
[improved version]

## Changes Made
- [change 1]: reason
- [change 2]: reason

## Optional Further Improvements
- [suggestion if applicable]
```

## Style Guidelines
- Prefer simple words over complex ones
- One idea per sentence
- Vary sentence length for rhythm
- Use specific nouns and strong verbs
- Avoid jargon unless the audience expects it
- Contractions are fine for casual tone, avoid for formal

## Rules
- Always preserve the author's meaning and intent
- Present ONE improved version (not multiple alternatives)
- If the text is already well-written, say so — don't change for the sake of change
- For non-English text, maintain the original language unless asked to translate
