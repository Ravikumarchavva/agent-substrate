# The L0-L3 Stack — Why, Not Just What

Root `CLAUDE.md` has the directory map and the import-linter contract table.
This doc is the *why* — the reasoning that should stop you from "just importing
it, it's easier" across a layer boundary.

## The rule

```
kernel (L0)       Pure contracts: Protocols, dataclasses, enums. No I/O.
    ↑ imported by
agents (L1)       Core intelligence: LLM loop, guardrails, middleware, agent types.
    ↑ imported by
capabilities (L2) What agents can do: tools, skills, knowledge/RAG, memory, stores.
    ↑ imported by
fabric (L3)       How agents are orchestrated: flows, evals, durable execution.
```

Enforced by `uv run lint-imports` (5 contracts, see `pyproject.toml`
`[tool.importlinter]`). CI fails if broken — this isn't a suggestion.

## Why kernel is frozen

`kernel/` has zero I/O and zero external dependencies by design. The payoff:
every backend (in-memory, Postgres, Redis, S3, whatever comes next) can
implement a kernel Protocol and be swapped without touching `agents/`,
`capabilities/`, or `fabric/`. This is what makes the Stage 0 → Stage 1 → Stage 2
runtime migration (see [`runtime-stages.md`](runtime-stages.md)) possible without
a rewrite — the `Agent.run(ctx, inbox)` contract and all the runtime Protocols
(`EventLog`, `Inbox`, `Scheduler`, `SignalBus`, ...) don't change; only what's
injected behind them does.

Kernel invariants are enforced by `tests/architecture/test_kernel_invariants.py`:
a LOC ceiling (6k) and file-count ceiling (45) exist specifically to catch
"I'll just add one small concrete helper here" drift — that helper belongs in
`extensions/`-equivalent territory (`agents/` or `capabilities/`), not kernel.

## Why `agents` (L1) can't import `capabilities` (L2)

Agent *behavior* (the ReAct loop, guardrails, middleware pipeline) must not
depend on *what tools exist*. This is what lets you build a new agent type
without needing every tool wired up yet, and what keeps the tool catalog
(`capabilities/tools/`) hot-swappable per deployment (ravi-ui vs a custom
SaaS instance) without agents/ caring.

## Why `integrations/`, `infrastructure/`, `serving/` are orthogonal

These three are **not part of the vertical stack** — they're wiring, not
behavior:

- `infrastructure/` — built-in backends the engine itself runs on (Postgres,
  Redis, MinIO, the durable runtime backends). If you're building a Postgres-
  or Redis-backed implementation of a kernel Protocol, it goes here.
- `integrations/` — external third-party adapters (LLM providers, MCP, Spotify,
  email/calendar). If you're wrapping someone else's API, it goes here.
- `serving/` — deployment shells (monolith FastAPI app, the 12 microservices,
  the SSE wire protocol). This is where L0-L3 code gets assembled into a
  running server; it may cross-import all layers, which is why it's excluded
  from the strict layer contract but still bound by
  `serving cannot import agents or capabilities` (serving talks to agents
  through the `infrastructure/serving_factory.py` seam, not directly).

The seam matters: `infrastructure/serving_factory.py` is explicitly documented
as "the ONLY place in the codebase where serving/ and agents/capabilities
meet." If you're adding a new agent-construction path from a route handler,
it goes through that factory, not a fresh import in `serving/monolith/routes/`.

## Common mistake this catches

Reaching into `agents/core/react.py` from a new tool in `capabilities/tools/`
to "just check something about the agent state" — this is an L2→L1 import in
the wrong direction if it's importing *agent internals* rather than the
`ctx: RunContext` object that's already passed to every tool's `execute()`.
`RunContext` (in `agents/runtime/context.py`, L1) is the sanctioned crossing
point — capabilities *receive* it, they don't reach up to construct or inspect
agent internals directly.
