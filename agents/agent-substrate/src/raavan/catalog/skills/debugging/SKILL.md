---
name: debugging
description: Systematic debugging skill for diagnosing and fixing errors in code, using structured hypothesis-driven analysis.
version: "1.0"
license: MIT
allowed-tools: code_interpreter file_manager
category: development/execution
tags: [debug, error, trace, fix, diagnose, troubleshoot, bug, exception, stack]
aliases: [debug-code, troubleshoot, fix-error]
metadata:
  author: agent-framework
---

# Debugging Skill

Use this skill when the user reports a bug, error, exception, or unexpected behaviour in their code.

## Debugging Procedure

### Step 1 — Reproduce
- Ask the user how to reproduce the issue (input, commands, environment).
- If code is provided, read it carefully before making assumptions.
- Identify the exact error message, stack trace, or unexpected output.

### Step 2 — Isolate
- Narrow the scope: which file, function, or line is the failure originating from?
- Check recent changes — bugs often hide in the most recently modified code.
- Separate "works" vs "broken" cases to find the boundary.

### Step 3 — Hypothesize
- Form 2-3 plausible hypotheses for the root cause.
- Rank by likelihood (most common causes first):
  1. Typos, wrong variable names, off-by-one errors
  2. Incorrect types or None/null values
  3. Missing imports, wrong function signatures
  4. Race conditions, async/await mistakes
  5. Environment differences (versions, config, OS)

### Step 4 — Test Each Hypothesis
- For each hypothesis, describe the expected behaviour if it were the cause.
- Add targeted logging, print statements, or use code_interpreter to test.
- Eliminate hypotheses that don't match the evidence.

### Step 5 — Fix & Verify
- Apply the minimal fix that addresses the root cause.
- Re-run the reproduction steps to confirm the fix works.
- Check for regressions: does the fix break anything else?

### Step 6 — Explain
- Tell the user:
  - **What** the bug was (root cause in one sentence)
  - **Why** it happened (the mechanism)
  - **How** you fixed it (the change)
  - **Prevention** — how to avoid similar bugs (linting, types, tests)

## Anti-Patterns
- Do NOT guess without evidence. Always trace the actual execution path.
- Do NOT apply "shotgun debugging" (changing many things at once).
- Do NOT skip reproduction — "it works on my machine" is not a fix.
