---
name: code-explainer
description: Step-by-step procedure for reading, explaining, and documenting code files shared by the user.
version: "1.0"
license: MIT
metadata:
  author: agent-framework
  category: developer-tools
---

# Code Explainer Skill

Use this skill when the user pastes code or asks for an explanation of a code snippet, file, or architecture.

## Analysis Procedure

Follow these steps in order:

### 1. Identify the Language & Context
- Detect the programming language from syntax or file extension.
- Note any framework clues (imports, decorators, annotations).

### 2. High-Level Summary
- Describe what the code does in 2–3 sentences.
- Identify the primary pattern used (e.g., factory, observer, async loop).

### 3. Line-by-Line Walkthrough (for short code)
- Walk through key lines or blocks.
- Explain non-obvious constructs in plain language.
- If the code is >100 lines, focus on the top-level structure and explain
  key functions/classes only.

### 4. Potential Issues
- Flag any obvious bugs, anti-patterns, or security concerns.
- Suggest improvements if relevant, but do not rewrite unless asked.

### 5. Summary Table
Generate a Markdown table with the following columns:

| Symbol | Type | Purpose |
|--------|------|---------|

Populate it with classes, functions, and important variables.

## Response Style
- Use plain English; avoid jargon unless the user is clearly technical.
- Use fenced code blocks with syntax highlighting for any code examples.
- Keep explanations concise: aim for one paragraph per logical block.
