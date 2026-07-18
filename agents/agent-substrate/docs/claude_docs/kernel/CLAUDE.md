# Kernel Audits — Index

This folder holds a **chronological series of kernel audit reports**. Each
audit is a snapshot of the kernel's health at a point in time — its contracts,
what the concrete implementation actually delivers against them, and what's
broken, patched-over, or missing.

## Convention

- **One file per audit, dated: `YYYY-MM-DD-audit.md`.**
- **Never overwrite a past audit.** They're a historical record — you want
  to be able to see "was this broken 3 months ago too, or is it new?" Old
  audits stay as-is even after their findings are fixed.
- **Next audit → new file.** When you (or a future session) does another
  full kernel audit, write a fresh `YYYY-MM-DD-audit.md`. Open with a short
  "Since the last audit" section that says what got fixed, what's still open,
  and what's newly found — don't make the reader diff two files by hand.
- **A partial re-check of one finding** (e.g. "did the SignalBusProtocol fix land?")
  doesn't need a new dated file — update the relevant finding's status in the
  most recent audit file in place, with a note of when it was re-verified.
- **Every claim needs a citation** — `file:line`, a grep command, or a
  concrete trace ("I read X, which calls Y, which does Z"). No claims from
  memory or vibes; the kernel's own docstrings are sometimes aspirational
  (see the first audit for examples), so "the docstring says so" is not
  itself evidence that the behavior exists.

## Audits

| Date | Summary |
|---|---|
| [2026-07-02](2026-07-02-audit.md) | First full audit. Contracts are sound; concrete implementation doesn't yet deliver true stateless replay. Found: no `fold`/replay function exists anywhere, suspension primitives never update SchedulerProtocol status (systemic — affects `ask_human`, tool approval, and subagent `join()`), `Checkpoint` is referenced by 3 docstrings but doesn't exist, coarse-grained journaling around suspending tool calls loses HITL answers on crash, zero test coverage of the event-sourcing/replay contract itself. |
| [2026-07-12](2026-07-12-audit.md) | Different angle: is every kernel export honest — real consumer, or aspirational scaffolding? Every symbol in `kernel/__init__.py` traced against real call paths. Found and fixed: ~10 fully dead contracts deleted (Event/EventPublisher/EventSubscriber, ToolSpec/spec_of/FunctionSpec/ProviderSpec, ControlPayload/ProgressPayload, register_block_type/register_payload_type, ThinkingBlock/UIResourceBlock, three dead error classes); `Supervision.execution_budget` was propagated but never enforced — fixed; **headline finding**: tool-approval had three independently-built abstractions (kernel Protocol, capabilities module, SSE bridge) and none was wired to a live agent — a CRITICAL-risk tool call executed completely unguarded in production. Consolidated on the kernel Protocol. The durable-runtime core (`EventLogProtocol`/`SchedulerProtocol`/`SupervisorProtocol`/etc.) from the first audit's findings is now solid and was left alone. |

## Related docs (don't duplicate, cross-reference)

- [`../architecture/runtime-stages.md`](../architecture/runtime-stages.md) — the Stage 0/1/2 backend roadmap this audit series checks against.
- [`../architecture/hitl.md`](../architecture/hitl.md) — HITL-specific detail; the audit's `ask_human` finding is the deep-dive behind that doc's durability caveat.
- [`../decisions.md`](../decisions.md) — architecture decisions; an audit finding sometimes becomes a decision once acted on.
- [`../roadmap.md`](../roadmap.md) — the prioritized fix list; an audit's "what needs to be done" section feeds directly into roadmap priorities.
