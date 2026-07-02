# Runtime Stages — What's Actually Built vs. Planned

The kernel runtime Protocols (`kernel/runtime/*.py`) each document a staged
backend roadmap directly in their docstrings. This doc consolidates that
roadmap in one place and — critically — records what's **actually implemented
today** vs. what's aspirational, because the docstrings describe the target
architecture, not necessarily current reality.

Verified by grepping `Stage 0\|Stage 1\|Stage 2` across `src/substrate/kernel/`
— re-run that grep if this doc feels stale.

## The staged model, per Protocol

| Protocol | Stage 0 (built) | Stage 1 (built?) | Stage 2+ (not built) |
|---|---|---|---|
| `EventLog` | `InMemoryEventLog` | `PostgresEventLog` ✅ built (`infrastructure/runtime/pg_event_log.py`) | NATS JetStream / Kafka |
| `Inbox` | `InMemoryInbox` | `PostgresInbox` ✅ built | Redis Streams consumer group |
| `Scheduler` | `InMemoryScheduler` | `PostgresScheduler` ✅ built (`SELECT FOR UPDATE SKIP LOCKED`; `wake_signals`/`wake_at`/`cancel_requested`/`deadline` columns) | Redis / NATS work-queue, distributed consistent-hash |
| `SignalBus` | `InMemorySignalBus` ✅ consume-based | `PostgresSignalBus` ✅ built (`infrastructure/runtime/pg_signal_bus.py`) | — |
| `Supervisor` | `InMemorySupervisor` | `PostgresSupervisor` ✅ built (`infrastructure/runtime/pg_supervisor.py`) | cross-region placement |

`build_postgres_runtime()` (`infrastructure/runtime/factory.py`) now wires
**every** backend — `EventLog`/`Inbox`/`Scheduler`/`SignalBus`/`Supervisor` —
to Postgres. This is what `RUNTIME_BACKEND=postgres` (the **default**,
`config.py:52`) gets you as of Phase 1 PR4-PR7 (2026-07-03). The old
`RedisJournal`/`Journal` Protocol is gone from the durability path entirely
(removed in PR3) — `EventLog`'s own `effect.result` entries are the single
source of truth for effect dedup, folded into an `EffectCache` per lease.

## Suspended runs are now durable (fixed 2026-07-03)

This section used to document a live gap: suspended runs (`ask_human`,
`ctx.ask`, `ctx.join`, `ctx.sleep_until_signal/until`) were not actually
durable even in "Postgres mode," because `SignalBus` and `Supervisor` stayed
in-memory. **That gap is closed.** The mechanism that replaced it:

**`SuspendInterrupt` + replay-from-top** (`agents/runtime/context.py`,
`agents/runtime/worker.py`). Every suspension primitive follows the same
shape: a non-blocking `SignalBus.consume(run_id, name, effect_id)` call
either finds what it's waiting for, or raises `SuspendInterrupt` — a
`BaseException` (so `except Exception` in agent/tool code can't swallow it)
that unwinds straight out of `agent.run()` to the Worker. The Worker catches
it and calls `Scheduler.release(status=SUSPENDED, wake_on=...)`; the asyncio
Task then genuinely ends — zero RAM, zero CPU, and (critically) the
Postgres `ravi_run_queue.status` row is actually `'suspended'`, not stuck at
`'running'` the way it silently was before this fix. `reclaim_orphans()`
correctly leaves `'suspended'` rows alone.

Resume is just a fresh lease: any worker (this one or another process
entirely) picks up the row once something fires a matching signal or a
`wake_at`/`deadline` passes (both ride the scheduler's existing lease-poll
cadence — no separate timer service), folds a fresh `EffectCache` from the
EventLog, and calls `agent.run()` again from the top. Every already-completed
effect and every already-consumed signal is a cache/consume hit, so execution
fast-forwards silently back to the same wait point, which now succeeds.

**What this means concretely:** a monolith restart while a user has a
pending `ask_human` card in front of them is no longer destructive. The
card's underlying run is `'suspended'` in Postgres; a fresh process leasing
that row replays up to the same `sleep_until_signal()` call, re-claims the
same journaled `request_id` (see `AskHumanTool.request_id = await
ctx.uuid()`), and waits again — the user's eventual click delivers a signal
row any worker's `consume()` picks up.

`Supervisor.join()`'s old `asyncio.Event()`-blocking implementation (the
"systemic, not `ask_human`-specific" half of the original gap) is also
retired from the live path: `ctx.join()` now consumes a `child:{run_id}`
signal exactly like `ctx.ask()` does, fired by `Supervisor.finish_run()`
when the child reaches a terminal state. `InMemorySupervisor`/
`PostgresSupervisor.join()` still exist for Protocol conformance but nothing
calls them anymore — see their docstrings.

Full trace and the original failure evidence: the audit at
[`../kernel/2026-07-02-audit.md`](../kernel/2026-07-02-audit.md) predates
this fix and should be read as history, not current state — cross-check
any claim there against the code before trusting it.

## Known remaining gaps (Phase 1 PR8 / Phase 2+)

- **`ravi_run_queue.deadline` has no writer yet.** The column and its
  enforcement (`PostgresScheduler.lease()` terminal-fails a pending/suspended
  run past its deadline; `heartbeat()` returns `True` for a running one) are
  both live, but nothing sets `Scheduler.enqueue(..., deadline=...)` from
  `Supervision.execution_budget.deadline_s` yet — that budget field is still
  dead code, same gap the original audit flagged.
- **Deadline-fail doesn't notify a joining parent.** `PostgresScheduler` has
  no `Supervisor` reference, so a pending/suspended run that fails on its own
  deadline doesn't fire a `child:{run_id}` signal the way `finish_run()`
  does. A parent relying on `ctx.ask`/`ctx.join` is unaffected (those have
  their own independent timeout), but a parent with neither set and no other
  signal will not learn about the failure proactively.
- **Signal/event-log GC** — consumed `ravi_signals` rows and terminal-run
  EventLog entries have no retention sweep yet (PR8).
- **`FollowGraph`** is still in-memory only; no durable backend exists.

## Why this matters for new work

Today, `EventLog`/`Inbox`/`Scheduler`/`SignalBus`/`Supervisor` state all
survives a process restart under `build_postgres_runtime()`. If you're
building something that depends on "the run will still be there when X
responds," that assumption now holds across the whole coordination layer —
the one thing to still check is whether X's own wait has a deadline wired
(see the gap above) if you're relying on deadline-based cleanup rather than
an explicit signal.
