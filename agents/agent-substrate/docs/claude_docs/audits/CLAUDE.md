# System Architecture Audits — Index

This folder holds **whole-system architecture audits** (runtime + serving +
orchestration + tenancy + observability). Kernel-only audits live in
[`../kernel/`](../kernel/CLAUDE.md); the same conventions apply here:

- **One file per audit, dated `YYYY-MM-DD-system-audit.md`. Never overwrite
  a past audit** — they're the historical record.
- **Next audit → new file**, opening with a "Since the last audit" section.
- **Every claim needs a citation** — `file:line`, a grep, or a concrete trace.
- A partial re-check of one finding updates the finding's status in the most
  recent audit in place, with the re-verification date.

## Audits

!!! warning "2026-07-02 audit's findings are now mostly resolved — read as history, not current state"
    Phases 0-3 of the remediation program this audit triggered are marked
    **Done** in `../roadmap.md` (as of 2026-07-03/04): the coordination layer
    (`PostgresSignalBus`, `PostgresSupervisor`, `PostgresScheduler`) is durable
    Postgres today, not in-process memory; the cross-tenant IDOR is fixed;
    tenant_id is threaded end-to-end; `human_gate` now has real callers (see
    `../architecture/hitl.md`'s 2026-07-05 update). Use the table below to
    know *what was found*, not what's still true — check `roadmap.md`'s
    "Phase status" table for current state before assuming any specific
    finding is still open.

| Date | Summary |
|---|---|
| [2026-07-02](2026-07-02-system-audit.md) | First full system audit (3 parallel deep-dives: runtime execution path, serving/state, orchestration/messaging). Verdict *at the time*: single-run durability is real; the coordination layer (SignalBus, Supervisor, HITL, cancel, locks, fanout) is in-process memory even in Postgres mode — blocks horizontal scaling AND restart-safety. Cross-tenant IDOR found. Budgets/deadlines/fairness/agent-loop-tracing are dead or unwired code. Microservices are scaffolding (human_gate has zero callers). Led directly to the approved remediation program (see plan / roadmap) — **most of this verdict is now resolved, see the warning above.** |

## Related

- [`../kernel/CLAUDE.md`](../kernel/CLAUDE.md) — kernel-scoped audit series (2026-07-02 kernel audit is the deep-dive behind several findings here).
- [`../roadmap.md`](../roadmap.md) — the prioritized program derived from this audit.
- [`../architecture/runtime-stages.md`](../architecture/runtime-stages.md) — Stage 0/1/2 backend matrix these audits check against.
