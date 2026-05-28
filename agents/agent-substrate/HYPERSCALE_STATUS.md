# Hyperscale Kernel — Implementation Status

Single source of truth for the 16-section plan that takes `ravi.kernel` from
"in-process contracts" to "planet-scale distributed agent fabric." Updated
at the end of every section.

## Architecture rules (frozen — do not break)

1. **`kernel is independent`** — `ravi.kernel` may not import from
   `ravi.{extensions, integrations, catalog, server, services, shared,
   configs, logger}`. Enforced by `tool.importlinter` in `pyproject.toml`
   and by `tests/kernel/test_hardening_pass.py::TestB1KernelIndependence`.
2. **Layered downward dependencies**:
   `server | services ← catalog ← integrations ← extensions ← kernel`.
3. **Ceilings** (in `tests/architecture/test_kernel_invariants.py`):
   ≤ 15 000 LOC and ≤ 110 files inside `src/ravi/kernel/`.
4. **No concrete classes in `kernel/{agents,guardrails,middleware}/`** —
   only the base ABC + result/config dataclasses + enums.
5. **Free-threading-safe** — every shared mutable structure in the
   runtime path is guarded by `threading.RLock`.
6. **`uv run` only** — never `pip` or bare `python`/`pytest`.

## Section status

| #  | Section                              | Status | Tests added | Notes |
|----|--------------------------------------|--------|------------:|-------|
| 1  | Contract Foundation                  | ✅      | (pre)       | `TemporalSemantics`, `LocalityHint`, `Envelope` wired |
| 2  | Core Fabric Contracts                | ✅      | (pre)       | `PrincipalId`, `IdentityContext`, `AgentLifecycleState`, `DurableEventLog/RealtimeFanout/EventFabric`, `TrustContext`, `PlacementContract` |
| 0  | Hardening Pass (B1–B10)              | ✅      | +19         | Logger purge, dead fabric removed, `Envelope ⇆ EventEnvelope[T]` unified, `CheckpointRef ⇆ RunCheckpoint` reconciled, `_normalize_content` tightened |
| 3  | Runtime Redesign                     | ✅      | +24         | `LeaseRegistry`, `BackpressurePolicy`, lifecycle FSM, partition-affinity dispatch, RLock guards |
| 4  | Event Fabric Implementation          | ⚠️ partial | +15        | `InMemoryDurableLog/Fanout/Fabric` + `DistributedRuntime` proven. **Redis backends still to do.** |
| 5  | Identity Plane                       | ⚠️ partial | +19        | `RoutingMiddleware` Protocol + `IdentityRequired/TenantIsolation/DepthLimit/TrustDecay`. **JWT decoder + `Principal` ORM still to do.** |
| 6  | Trust Graph Plane                    | ✅      | +22         | `TrustGraph` Protocol, `InMemoryTrustGraph`, `TrustEnrichmentMiddleware` |
| 7  | Resource Scheduler                   | 🔲      |             | `FairShareScheduler`, spend authority, preemption, GPU/CPU placement |
| 8  | Metadata / Index Plane               | 🔲      |             | `MetadataStore` Protocol, Postgres + Redis tiers, hot-key compaction |
| 9  | Memory + Graph Redesign              | 🔲      |             | `LineageStore`, tier separation (Redis/Postgres/S3), provenance tagging |
| 10 | Ranking + Attention                  | 🔲      |             | Feed generation, trust-weighted scoring, sybil suppression |
| 11 | Governance + Political Dynamics      | 🔲      |             | Coalition detection, risk scorer, quarantine actuators |
| 12 | Self-Evolution Safeguards            | 🔲      |             | `MutationPermission`, family depth ceiling, circuit breakers |
| 13 | Economic Plane                       | 🔲      |             | `BudgetLedger`, spend enforcement, loop/farming detection |
| 14 | Observability + Replay               | 🔲      |             | Span-per-envelope, replay gate, operator kill-switch |
| 15 | Semantic Consistency                 | 🔲      |             | `SemanticInvariant`, invariant checker, divergence detection |
| 16 | Control Plane / Multi-Region         | 🔲      |             | Cached hot-path reads, local fallbacks, region-local routing |

**Cumulative**: 515 → **769 passing tests**, 0 ruff errors, 0 upward imports.

## Where things live

### Kernel contracts (frozen, do not edit to add features)

| Contract                       | File                                                                                      |
|--------------------------------|-------------------------------------------------------------------------------------------|
| `Envelope` (in-process)        | [`runtime/_contracts.py`](src/ravi/kernel/runtime/_contracts.py)                          |
| `EventEnvelope[T]` (wire)      | [`contracts/_event.py`](src/ravi/kernel/contracts/_event.py)                              |
| `TemporalSemantics, LocalityHint, TrustContext, PlacementContract` | [`contracts/_coordination.py`](src/ravi/kernel/contracts/_coordination.py) |
| `PrincipalId, IdentityContext, DelegationToken` | [`runtime/_identity.py`](src/ravi/kernel/runtime/_identity.py) |
| `AgentLifecycleState, ExecutionLease, AgentActivationContract` | [`runtime/_lifecycle.py`](src/ravi/kernel/runtime/_lifecycle.py) |
| `LeaseRegistry`                | [`runtime/_lease.py`](src/ravi/kernel/runtime/_lease.py)                                  |
| `BackpressurePolicy, BackpressureSignal` | [`runtime/_backpressure.py`](src/ravi/kernel/runtime/_backpressure.py)          |
| `RoutingMiddleware`            | [`runtime/_middleware.py`](src/ravi/kernel/runtime/_middleware.py)                        |
| `DurableEventLog, RealtimeFanout, EventFabric` | [`events/_fabric.py`](src/ravi/kernel/events/_fabric.py)                  |
| `TrustGraph, TrustScore, ProvenanceChain` | [`contracts/_trust.py`](src/ravi/kernel/contracts/_trust.py)                   |

### Extension implementations (this is where new capability lands)

| Module                        | What's there                                                                                  |
|-------------------------------|------------------------------------------------------------------------------------------------|
| `extensions/events/`          | `InMemoryDurableLog`, `InMemoryRealtimeFanout`, `InMemoryEventFabric`                          |
| `extensions/runtime/`         | `DistributedRuntime`, 5 routing middlewares                                                    |
| `extensions/trust/`           | `InMemoryTrustGraph`                                                                           |
| `extensions/economic/`        | 🔲 (S13)                                                                                       |
| `extensions/metadata/`        | 🔲 (S8)                                                                                        |
| `extensions/safeguards/`      | 🔲 (S12)                                                                                       |
| `extensions/observability/`   | 🔲 (S14)                                                                                       |
| `extensions/semantics/`       | 🔲 (S15)                                                                                       |
| `extensions/scheduler/`       | 🔲 (S7)                                                                                        |

## Standing in-flight deferrals

These were deliberately punted as **non-architectural SDK / web-adapter
work**. They can land any time without disturbing the kernel.

- **Section 4 (Event Fabric) — Redis backends**: `RedisStreamsDurableLog`,
  `RedisPubSubFanout`, `RedisLeaseRegistry`. Mechanical: each one wraps a
  ~150-line `redis.asyncio` call sequence against the same kernel
  Protocols. Location: `integrations/events/`.
- **Section 5 (Identity Plane) — Web boundary**: `Principal` ORM model,
  `PrincipalStore` Protocol, JWT → `IdentityContext` decoder, delegation
  token persistence. Location: split across `integrations/identity/` and
  `server/security/`.

## Parallelization plan for the remaining sections

The following sections can run **in isolation** — they touch separate
kernel subpackages and separate extension directories, so multiple
contributors (or subagents) can work simultaneously without merge
conflict.

| Track | Section | Kernel subpackage              | Extension subpackage          | Blocks?       |
|-------|---------|--------------------------------|-------------------------------|---------------|
| A     | 13 Economic Plane                | `kernel/economic/`            | `extensions/economic/`        | None — S7 will read from it later |
| B     | 8 Metadata / Index Plane         | `kernel/metadata/`            | `extensions/metadata/`        | None |
| C     | 12 Self-Evolution Safeguards     | `kernel/safeguards/`          | `extensions/safeguards/`      | None |
| D     | 14 Observability + Replay        | `kernel/observability/`       | `extensions/observability/`   | None — already imported broadly via `logging` |
| E     | 15 Semantic Consistency          | `kernel/semantics/`           | `extensions/semantics/`       | None |

Each track creates **new directories**; none modify existing files except
to add lines to its own `__init__.py`. Top-level `kernel/contracts/__init__.py`
and `extensions/__init__.py` are reconciled at review time, not by the
subagents themselves, to avoid concurrent-edit conflicts.

Tracks that need sequencing afterwards:

- **S7 (Resource Scheduler)** reads `BudgetLedger` from S13 → runs after.
- **S9 (Memory + Graph)** touches `kernel/memory/` which has live
  references; needs careful surgery and a single owner.
- **S10 (Ranking)** + **S11 (Governance)** both consume `TrustGraph` (S6,
  done) plus possibly S13 budget signals — run after S13.
- **S16 (Multi-Region)** needs the fabric (S4) Redis backends — runs after
  the Section 4 deferrals close.

## Acceptance gates for every section

A section is "done" only when:

1. New contracts live in `kernel/<section>/` and import only from `kernel/`
   and stdlib (verified by `TestB1KernelIndependence`).
2. Reference implementations live in `extensions/<section>/`.
3. Unit tests cover Protocol conformance + behavior + edge cases.
4. End-to-end test composes the new feature with the existing pipeline
   (`LocalRuntime` + routing middleware) where applicable.
5. Full `uv run --no-sync pytest tests/` passes with **zero new failures**.
6. `uv run ruff check` clean on changed files.
7. `tests/architecture/test_kernel_invariants.py` still green (LOC + file
   ceilings, no concrete agents/guardrails/middleware in kernel).
8. This status document is updated.
