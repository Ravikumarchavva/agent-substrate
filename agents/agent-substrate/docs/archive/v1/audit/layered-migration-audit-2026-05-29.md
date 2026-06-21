# Layered Migration Audit — ravi-engine

**Date**: 2026-05-29  
**Scope**: `src/ravi/` after the six-layer migration (`fabric/`, `reasoning/`, `orchestration/`, `guardrails/`, `platform/`)  
**Question asked**: Is the code actually good, or are there patches instead of real fixes?

**Verdict**: The *physical* move was done well — code was relocated into sensible layer directories and the new modules are real, complete implementations (not stubs). But the *logical* migration was finished with **backwards-compatibility patches, not real fixes**. The headline guarantee of the whole exercise — a frozen, independent L0 kernel — is currently **inverted**: the kernel now sits at the *top* of the dependency graph, and CI reports it as healthy because the enforcement was never updated.

---

## Summary

| # | Finding | Severity | Nature |
|---|---------|----------|--------|
| 1 | `extensions/` is a broken zombie tree (dead, un-importable duplicates) | **Critical** | Leftover — not deleted |
| 2 | Kernel re-export shims invert the dependency graph (kernel imports L1–L5) | **Critical** | **Patch, not fix** |
| 3 | import-linter reports a false green — no layered contract exists | **Critical** | Enforcement gap |
| 4 | Layer-assignment bugs: `resilience`/`batch` placed above their consumers | **High** | Wrong design |
| 5 | `fabric/` (L1) imports up into `guardrails/` (L4) & `platform/` (L5) | **High** | Workaround |
| 6 | Architecture-invariant tests stale (ceilings raised, point to dead `extensions/`) | **Medium** | Not updated |
| 7 | Test tree not reorganized to match layers | **Medium** | Incomplete |
| 8 | Stale docstrings reference dead `agent_substrateextensions.*` | **Low** | Cosmetic |

What went **right**: kernel LOC dropped from ~16,900 → ~10,000; the concrete runtime (`LocalRuntime`, `SagaCoordinator`, dispatcher, mailbox, supervisor) genuinely moved to `fabric/`; the new-layer files are larger and more complete than the originals; the contract/impl split for routing middleware (`kernel/runtime/_middleware.py` = ABC, `fabric/runtime/_middleware.py` = built-ins) is exactly right.

---

## Finding 1 — `extensions/` is a broken zombie tree

### Problem

The migration **copied** code into the new layers but never **deleted** the old tree. Worse, `extensions/` is no longer even importable — its modules import things that were moved out from under them:

```
$ uv run python -c "import agent_substrateextensions.pipelines"
ModuleNotFoundError: No module named 'agent_substrateextensions.pipelines.middleware'

$ uv run python -c "from agent_substrateextensions.agents.assistant.agent import AssistantAgent"
ModuleNotFoundError: No module named 'agent_substrate.kernel.agents.actor'
```

`extensions/agents/assistant/agent.py` still does `from agent_substrate.kernel.agents.actor import ActorAgent` — but `ActorAgent` moved to `agent_substrate.fabric.actors.actor`. The files that survive in `extensions/` reference siblings that were never copied (`extensions.pipelines.middleware`, `extensions.pipelines._expr_eval`, `extensions.resilience.policies`, `extensions.agents.flow`, `extensions.structured`, `extensions.tools`).

It is also **dead**: nothing in `src/ravi/` outside `extensions/` itself imports `agent_substrateextensions` (0 references). It is a 9-file, ~3,900-LOC stale duplicate of `reasoning/` and `orchestration/`.

```
src/ravi/extensions/
├── agents/assistant/agent.py          ← stale dup of reasoning/agents/assistant/agent.py (differ)
├── agents/assistant/_tool_execution.py
├── context/__init__.py                ← imports context.* siblings that don't exist here
├── middleware/retry.py
└── pipelines/{runner,condition_runner,while_runner,codegen}.py  ← stale dup of orchestration/workflows/
```

### Fix (real, not a patch)

Delete the entire `src/ravi/extensions/` directory. Then remove every remaining reference to `agent_substrateextensions` (import-linter config, architecture tests, docstrings — see findings 3, 6, 8). This is safe: the tree cannot be imported and nothing depends on it.

---

## Finding 2 — Kernel re-export shims invert the dependency graph *(the main "patch")*

### Problem

This is the single biggest "patch rather than fix." To keep old import paths like `from agent_substrate.kernel.agents import ActorAgent` working after the code moved up to `fabric/`, the migration left **module-level re-export shims inside the kernel that import upward** from L1–L5:

| Kernel file (L0) | Imports upward from | Layer |
|---|---|---|
| `kernel/agents/__init__.py` | `agent_substrate.fabric.actors.actor` | L1 |
| `kernel/memory/__init__.py` | `agent_substrate.fabric.memory.unbounded` | L1 |
| `kernel/storage/__init__.py` | `agent_substrate.fabric.storage.local` | L1 |
| `kernel/plugin/registry.py` | `agent_substrate.fabric.actors.actor` | L1 |
| `kernel/middleware/runner.py` | `agent_substratereasoning.middleware.pipeline` | L2 |
| `kernel/execution/__init__.py` | `agent_substratereasoning.middleware.pipeline` | L2 |
| `kernel/observability/__init__.py` | `agent_substrateguardrails.killswitch`, `agent_substrateplatform.observability.*` | L4 + L5 |

The frozen, independent L0 kernel that the whole refactor exists to create is now the **apex** of the dependency graph — it transitively depends on fabric, reasoning, guardrails, and platform. And because `fabric/actors/actor.py` imports right back into `kernel` (`kernel.messages.content`, `kernel.runtime._identity`, `kernel.runtime._protocol`, …), there is a genuine **circular dependency `kernel ↔ fabric`**. It happens to not deadlock at import time today, but it is exactly the architecture the layering was meant to eliminate.

`kernel/plugin/registry.py` is especially telling: a comment says the base-class imports are done "lazily inside `_bind()` … to avoid circular imports," but right below it the `ActorAgent` import was **hoisted to module level** — the comment now describes code that no longer exists.

### Fix (real, not a patch)

Re-exports for backwards compatibility must point **downward or sideways**, never up. Two clean options:

1. **Preferred** — update call sites to import from the real home (`from agent_substrate.fabric.actors.actor import ActorAgent`) and delete the kernel shim entirely. The kernel `__init__.py` files should export only what physically lives in the kernel.
2. **If a transition shim is truly needed**, put it in the layer that *owns* the symbol (e.g. a `fabric/__init__.py` convenience export), not in the kernel. The lower layer must never name a higher one.

For `kernel/plugin/registry.py`, restore the lazy import inside `_bind()` (the comment already describes the correct design) so the registry module does not import `fabric` at module load.

---

## Finding 3 — import-linter reports a false green

### Problem

`uv run lint-imports` prints **"kernel is independent KEPT … 1 kept, 0 broken"** — while finding 2 shows the kernel importing four layers above it. The check passes because the contract only forbids the **old** module names:

```toml
[[tool.importlinter.contracts]]
name = "kernel is independent"
type = "forbidden"
source_modules = ["agent_substrate.kernel"]
forbidden_modules = [
    "agent_substrateextensions",      # ← now a dead tree
    "agent_substrate.integrations", "agent_substratecatalog", "agent_substrateserver",
    "agent_substrateservices", "agent_substrateshared", "agent_substrate.configs", "agent_substrate.logger",
]
# fabric / reasoning / orchestration / guardrails / platform are NOT listed
```

The five new layers are absent from the forbidden list, so the kernel→fabric/reasoning/guardrails/platform imports are invisible to CI. This is precisely the "the frozen constraint is unenforceable" failure mode the design doc warned about — the green check is now actively misleading.

### Fix (real, not a patch)

Replace the single forbidden contract with a **layered contract** that encodes the real intended order, plus keep a forbidden contract for the non-layer modules:

```toml
[[tool.importlinter.contracts]]
name = "ravi layers"
type = "layers"
layers = [
    "agent_substrateplatform",       # L5 (highest)
    "agent_substrateguardrails",     # L4
    "agent_substrateorchestration",  # L3
    "agent_substratereasoning",      # L2
    "agent_substrate.fabric",         # L1
    "agent_substrate.kernel",         # L0 (lowest)
]

[[tool.importlinter.contracts]]
name = "kernel imports nothing in the app"
type = "forbidden"
source_modules = ["agent_substrate.kernel"]
forbidden_modules = [
    "agent_substrate.fabric", "agent_substratereasoning", "agent_substrateorchestration",
    "agent_substrateguardrails", "agent_substrateplatform",
    "agent_substrateextensions", "agent_substrate.integrations", "agent_substratecatalog",
    "agent_substrateserver", "agent_substrateservices",
]
```

This will (correctly) go **red** until findings 2, 4, and 5 are fixed. That red is the whole point — it is the mechanical guarantee the doc promised. Land the contract first; let it fail; fix the imports until it's green.

---

## Finding 4 — Layer-assignment bugs: utilities placed above their consumers

### Problem

Two modules were assigned to layers *above* the code that uses them, creating backwards dependencies that no amount of shimming can make clean:

**`guardrails/resilience/` (L4) → used by L2 and L3.** Retry/backoff is not a safety policy; its own docstring says *"Retry and resilience **utilities** for production agent workloads."* Yet it lives in L4 Guardrails and is imported by:
- `reasoning/agents/assistant/agent.py` (L2)
- `reasoning/agents/assistant/_tool_execution.py` (L2)
- `reasoning/middleware/retry.py` (L2)
- `orchestration/agents/orchestrator/agent.py` (L3)

So L2/L3 depend on L4 — the inversion the layering was meant to kill.

**`platform/batch/` (L5) → used by L2.** `reasoning/extraction/extractor.py` (L2) imports `from agent_substrateplatform.batch.processor import BatchProcessor`. L2 depends on L5.

### Fix (real, not a patch)

These are design errors, not import-path errors — fix placement, not the imports:

- Move `resilience/` (retry policy + `_calculate_delay` + `retry_async`) **down** to L1 `fabric/` (or a small shared utilities module). It is infrastructure every layer may use. This also finally resolves Issue 2 from the earlier cleanup audit (duplicate retry math) — there should be exactly one retry implementation, and it should live below everything that retries.
- `BatchProcessor`: either it is a reasoning-time fan-out helper (move to L2 `reasoning/`) or `extraction` is itself a platform/eval concern (move the *consumer* up). Decide based on whether batch processing is part of single-agent reasoning or an operator-level capability. Given `extraction` runs inside the agent loop, moving `BatchProcessor` down to L2 is the likely correct call.

---

## Finding 5 — `fabric/` (L1) imports up into `guardrails/` (L4) and `platform/` (L5)

### Problem

18 references from `fabric/` reach up into L4/L5. Most are `TYPE_CHECKING`-only or function-local imports — and one carries a revealing comment:

```python
# fabric/runtime/_middleware.py
from agent_substrateguardrails.mutation._breaker import CircuitOpen  # local to avoid kernel→extensions
```

A "local import to avoid a cycle" is the canonical symptom of a layer boundary fighting the real call graph. But several are genuine **module-level** upward imports:

```python
# fabric/runtime/_distributed.py
from agent_substrateplatform.scheduling._contracts import ResourceClaim, SlotGrantStatus, SchedulerContract
```

The fabric runtime needs scheduler **contracts**, governance **contracts**, kill-switch **contracts**, span **contracts** — but those contracts currently live in L4/L5 *implementation* packages.

### Fix (real, not a patch)

The contracts (Protocols / value objects) that fabric depends on belong in L0 `kernel/`, with their *implementations* staying in L4/L5. This is the contract/impl split done correctly:
- `SchedulerContract`, `ResourceClaim`, `SlotGrant`, `PreemptionSignal` → `kernel/` (Protocols), Temporal/concrete scheduler → `platform/scheduling/`.
- `EnvelopeSpanRecorder`, `ReplayGate` Protocols → `kernel/`, OTel/replay impls → `platform/observability/`.
- `CircuitBreaker`/`CircuitOpen`: if the fabric needs to *raise/catch* it on the dispatch path, the exception + Protocol belong in `kernel/`; the policy impl stays in `guardrails/`.

Once the contracts are in L0, fabric imports **down** (kernel) instead of **up** (guardrails/platform), and the local-import workarounds disappear.

---

## Finding 6 — Architecture-invariant tests are stale

### Problem

`tests/architecture/test_kernel_invariants.py` was not updated for the new structure, and its ceilings moved the **wrong direction**:

```python
# comment claims "current values are ~15.3k LOC and 102 files"  (actual now: ~10k LOC)
MAX_KERNEL_LOC   = 20_000   # doc intent was to LOWER this after stripping the kernel
MAX_KERNEL_FILES = 130
```

The kernel is now ~10,000 LOC, so a 20,000 ceiling gives 2× headroom and no longer guards anything. The failure messages and the forbidden-import allowlist still tell contributors that concrete code "must live in `ravi/extensions/`" — a directory that is dead (finding 1).

### Fix

- Lower `MAX_KERNEL_LOC` to ~12,000 and `MAX_KERNEL_FILES` to ~110 (≈20% headroom over the real current size) so the ceiling regains its purpose. Tighten further after finding 2 is fixed (the shims drop kernel LOC again).
- Replace all `ravi/extensions/` references in messages and the forbidden list with the correct destination layers (`fabric/`, `reasoning/`, `guardrails/`).
- Update the stale "~15.3k LOC and 102 files" comment.

---

## Finding 7 — Test tree not reorganized to match layers

### Problem

Source moved to `fabric/ reasoning/ orchestration/ guardrails/ platform/`, but `tests/` still mirrors the old shape: `tests/extensions/guardrails/` exists; there is no `tests/fabric/`, `tests/reasoning/`, `tests/orchestration/`, or `tests/platform/`. Tests still import via the kernel shims (finding 2), which is why they pass — they will break the moment the shims are removed.

### Fix

Mirror the new layer layout under `tests/` and update imports to the real homes. Do this together with finding 2 so the shim removal and the test updates land in one consistent change.

---

## Finding 8 — Stale docstrings reference dead `agent_substrateextensions.*`

### Problem

Kernel `__init__.py` docstrings still describe the old world, e.g. `kernel/agents/__init__.py`:

> *Concrete agent implementations (`AssistantAgent`, …) live in `agent_substrateextensions.agents`.*

They now live in `agent_substratereasoning.agents` / `agent_substrateorchestration.agents`. Same stale pointer in `kernel/observability/__init__.py` and others.

### Fix

Sweep kernel docstrings and the architecture-test messages, repointing `agent_substrateextensions.*` to the correct layer. Low effort; do it alongside finding 1's deletion.

---

## Recommended order of work

The findings are coupled — do them as one coherent pass, not piecemeal:

1. **Land the layered import-linter contract (finding 3) first, and let it fail.** The red list becomes your exact worklist.
2. **Delete `extensions/` (finding 1)** — removes dead noise and several false references immediately.
3. **Fix misplacements (findings 4 & 5):** move `resilience` → `fabric/`, decide `batch`, lift fabric-needed contracts (`SchedulerContract`, span/replay/breaker Protocols) into `kernel/`.
4. **Remove the kernel re-export shims (finding 2):** repoint call sites downward; restore the lazy import in `plugin/registry.py`.
5. **Re-run `lint-imports`** — it should now be genuinely green, and "kernel is independent" finally true.
6. **Update tests (findings 6 & 7)** and **docstrings (finding 8)** to match.

The structure the migration produced is sound; the work that remains is to make the dependencies actually flow the way the directory names claim, and to make CI enforce it. Right now the layering is real on disk but fictional in the import graph.
