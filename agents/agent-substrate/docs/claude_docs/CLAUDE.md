# Claude's Working Docs — Index

This folder is Claude's persistent working memory for `agent-substrate`: deeper
architecture notes, an honest roadmap, recorded decisions, and debugging
playbooks that don't belong in the root `CLAUDE.md` (which stays a fast
orientation reference) but are too valuable to re-derive from scratch every
session.

**Read this file first.** It tells you what exists and when to open each doc.
The root `CLAUDE.md` links here — you should already be here if you followed it.

---

## What's in here

| File | Read it when... |
|---|---|
| [`architecture/layers.md`](architecture/layers.md) | You need the *why* behind the L0-L3 stack, not just the *what* (root CLAUDE.md has the what). |
| [`architecture/runtime-stages.md`](architecture/runtime-stages.md) | You're touching `agents/runtime/`, `infrastructure/runtime/`, or anything durability-related — tells you what's Stage 0 (in-memory) vs Stage 1 (Postgres) vs not-yet-built. |
| [`architecture/hitl.md`](architecture/hitl.md) | You're touching human-in-the-loop: `ask_human`, tool approval, or the microservices `human_gate`. Explains all three mechanisms and why they currently diverge. |
| [`architecture/prompt-and-skills.md`](architecture/prompt-and-skills.md) | You're adding/editing a system prompt section, a skill, a tool description, or a conditional instruction block in `chat_intents.py` — tells you which one owns a given piece of guidance, so it doesn't get restated in two places. |
| [`kernel/`](kernel/CLAUDE.md) | You need to know the kernel's *actual* health — a dated series of full audits (contracts vs. what's really implemented, what's broken/patched, what's next). Not a summary; the deep evidence lives here. |
| [`audits/`](audits/CLAUDE.md) | Whole-system architecture audits (runtime + serving + orchestration + tenancy). The 2026-07-02 audit is the evidence base for the active remediation program in `roadmap.md`. |
| [`roadmap.md`](roadmap.md) | Starting a new work session and want to know what's actually next, ranked by real impact — not a wishlist. |
| [`decisions.md`](decisions.md) | You're about to re-litigate an architecture choice ("why not just use a Future here?") — check here first, it's probably already been decided and reasoned through. |
| [`playbooks/debugging-suspended-runs.md`](playbooks/debugging-suspended-runs.md) | A run looks stuck / a HITL card isn't resolving / "it's not pausing." Concrete DB queries and log greps, not theory. |
| [`playbooks/adding-a-tool.md`](playbooks/adding-a-tool.md) | Writing a new tool, especially one that suspends the run (like `ask_human`) or needs approval. |

---

## How to keep this useful

- **Update, don't accumulate.** These are living documents, not a session log.
  When something in `roadmap.md` ships, move it out (to "recently shipped" or
  delete it) rather than leaving it stale. When a decision changes, edit
  `decisions.md` in place and note the date it changed.
- **Cite file:line, not vibes.** Every claim in these docs should be traceable
  to actual code. If you're not sure something is still true, verify before
  trusting it — these docs decay just like the root CLAUDE.md does.
- **New architecture area → new file under `architecture/`.** Don't let
  `layers.md` or `hitl.md` become a dumping ground for unrelated topics.
- **New recurring debugging pattern → new file under `playbooks/`.** If you
  find yourself re-deriving the same set of DB queries or log filters twice,
  it belongs here.
- **Don't duplicate the root `CLAUDE.md`.** That file is the fast-orientation
  reference (commands, directory map, coding standards). This folder is for
  things that need more room: rationale, tradeoffs, gaps, history.
