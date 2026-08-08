# Prompt & skill instruction ownership

Where does a new piece of model-facing guidance go? This repo has five
different injection points (`default_system.md`, tool `description` fields,
`chat_intents.py`'s conditional blocks, `SKILL.md` bodies, and a skill's
`references/` files), and nothing used to say which one owns what — so the
same rule got added in two places in one session (`default_system.md`'s
Human Input section and `chat_intents.py::ATTACHMENT_ANALYSIS_INSTRUCTIONS`
both independently grew "an attached file already answers what a report
should be based on"). This doc is the fix: a decision table, checked before
adding guidance, not after.

## The table

| Guidance is... | Lives in... | Not in... |
|---|---|---|
| Cross-cutting, true for every request (formatting, general principles) | `serving/monolith/prompts/default_system.md` | duplicated into skills or tool descriptions |
| How to correctly use one specific tool | that tool's own `description` field | `default_system.md` — a tool's usage rules travel with the tool, not a separate always-on prose block |
| A contextual delta, only relevant when a real signal fires (attachment present, mail intent, calendar-write intent) | the matching block in `serving/monolith/routes/chat_intents.py`, appended only when its gate fires | `default_system.md`, which is always-on and can't scope itself to "only when X is true" |
| A procedure for one class of task, activated on demand | that skill's `SKILL.md` body | restated elsewhere — cross-reference in one line instead |
| Correctness of generated output (a chart, a file, a report) | a verification check the skill runs on its own saved output (open the file, assert on what's actually there — see `excel_report/SKILL.md` Step 5) | an import mechanism trying to force the model to reuse specific code. Tried this once (`excel_report/scripts/`) — the model still wrote fresh code for data shapes the worked example didn't cover, so the import wasn't reliable, and it cost real sandbox-staging plumbing for that unreliable benefit. Deleted; the saved-file check catches the bug either way, regardless of how the code was written. |
| Detail that's only needed sometimes, not on every activation | that skill's `references/*.md`, read via `skills(action=read_reference, ...)` | the `SKILL.md` body itself, which is returned in full on every activation |

**Rule: before adding guidance, check this table for who already owns it.
If it's owned, cross-reference in one line — don't restate it.**

## Extension checklist — adding a new skill

1. `capabilities/tools/skills/<name>/SKILL.md`, frontmatter needs `name`
   (lowercase, hyphenated, 1-64 chars) and `description` (1-1024 chars) —
   both validated by `SkillMetadata.__post_init__`. `version`/`license`/
   `allowed-tools`/`metadata` are optional and cosmetic (surfaced in the CLI
   and `to_dict()`, not enforced). Don't add new frontmatter fields unless
   something actually reads them — `category`/`tags`/`aliases` existed for a
   while doing nothing before being removed for exactly this reason.
2. Keep the body under **150 lines** (`tests/architecture/test_skill_invariants.py`
   enforces this in CI). Over that, split detail into `references/*.md` —
   see `excel_report/` for the pattern: `SKILL.md` stays the step-by-step
   workflow, `references/functions.md` and `references/example.md` hold the
   code the model only needs once it's actually building something.
3. Anti-patterns/"what bad output looks like" bullets are worth including
   (they're concrete, not restated rules) — but check the table above before
   writing a new rule; it might already belong to `default_system.md` or a
   tool description.
4. Run `uv run pytest tests/architecture/test_skill_invariants.py` — it
   fails loudly on a missing description, a duplicate name, or an oversized
   body. `SkillLoader` itself stays lenient at runtime (a broken skill is
   logged and silently skipped, not fatal — a typo shouldn't take down the
   server), so this test is the only thing that actually catches it before
   merge.

## Extension checklist — adding a new conditional instruction block

1. Does it belong in `default_system.md` instead (always true, not
   contextual)? If so, put it there and stop here.
2. Otherwise: add a function to `serving/monolith/routes/chat_intents.py`
   that returns `""` when its condition doesn't apply, or the instruction
   text when it does — see `existing_task_board_block`/`attachments_block`/
   `custom_instructions_block` for the pattern (pure text, no side effects).
   If the block also needs to filter `tools` or force a `tool_choice` (like
   `_configure_workspace_mail_request`), that's a different, heavier shape —
   don't force it into the pure-text pattern.
3. Wire it into `chat.py`'s instruction-assembly loop (or the mail/calendar
   sequence, if it has side effects) **in the position that matches its
   actual priority** — assembly order is primacy, and primacy is why the
   model didn't reliably notice an attachment was present in an earlier
   version of this prompt (the attachment notice was appended after the
   entire skill roster). Don't just tack a new block onto the end.

## Assembly order (as of this doc)

1. `default_system.md` (always)
2. Skill roster (`SkillManager.system_prompt_suffix()`)
3. Existing task board hint (if one exists for this thread)
4. Attached files + `ATTACHMENT_ANALYSIS_INSTRUCTIONS` (if attachments)
5. Workspace mail instructions, else calendar-write instructions (if either
   intent is detected — mutually exclusive, mail checked first)
6. Per-request custom instructions from the frontend (if provided)

This list is the actual system-of-record for order; if you change it, update
this section in the same change.
