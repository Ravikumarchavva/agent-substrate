# Codebase Cleanup Audit — ravi-engine

**Date**: 2026-05-29  
**Scope**: `src/ravi/` — all layers (kernel, extensions, catalog, integrations, shared, server)  
**Focus**: duplicate implementations, diverged code, layering violations, naming confusion

---

## Summary

| # | Issue | Files | Severity | Action |
|---|-------|-------|----------|--------|
| 1 | EventBus is concrete in `shared/` | `shared/events/bus.py` | **High** | Move to `integrations/` |
| 2 | Retry backoff duplicated | `middleware/retry.py` + `resilience/policies.py` | **High** | Deprecate `RetryMiddleware` |
| 3 | Six context classes in one file | `extensions/context/redis_model_context.py` | **Medium** | Split into 6 files |
| 4 | `ServerContext` misnamed | `server/context.py` | **Medium** | Rename to `ServerDependencies` |
| 5 | Tool parsing duplicated | `assistant/_tool_execution.py` + `catalog/tools/_tool_executor.py` | **Medium** | Extract to `kernel/tools/` |
| 6 | "Pipeline" means two different things | `extensions/pipelines/` + `catalog/_pipeline.py` | **Medium** | Rename one |
| 7 | Lineage validation duplicated | `kernel/memory/_lineage.py` + `extensions/memory/_lineage.py` | **Low** | Extract constants |

---

## Issue 1 — EventBus is a concrete class inside `shared/`

### Problem

`shared/` is supposed to hold cross-service contracts (interfaces, envelope types, DI wiring). But `shared/events/bus.py` contains a fully concrete `EventBus` class that directly imports `redis.asyncio`, connects to Redis Streams, manages consumer groups, and handles OTel tracing.

The kernel already defines the right abstractions in `kernel/events/_fabric.py`: `EventFabric`, `DurableEventLog`, and `RealtimeFanout` Protocols. The concrete implementation should live in `integrations/`, not `shared/`.

### Current state

```
kernel/events/_fabric.py          ← EventFabric Protocol (correct)
shared/events/bus.py              ← CONCRETE EventBus class (wrong layer)
integrations/events/_redis_fanout.py   ← Redis pub/sub
integrations/events/_redis_log.py      ← Redis Streams log
integrations/events/_redis_lease.py    ← worker coordination
```

The three files under `integrations/events/` implement the right split (durable vs. fanout), yet `shared/events/bus.py` duplicates Redis Streams logic in the wrong layer and is imported throughout services as if it were a contract.

### Fix

1. Move `shared/events/bus.py` → `integrations/events/redis_event_bus.py`
2. Re-export from `shared/events/__init__.py` under the same name for backwards compatibility during transition
3. Add a factory function in `shared/events/factory.py`:
   ```python
   def get_event_bus(config: Settings) -> EventFabric: ...
   ```
4. Update lifespan wiring in `server/app.py` and each microservice to use the factory

---

## Issue 2 — Retry backoff logic duplicated between two modules

### Problem

Exponential backoff with jitter is implemented independently in two places:

**`extensions/resilience/policies.py`** — parameterised, reusable:
```python
def _calculate_delay(attempt: int, policy: RetryPolicy) -> float:
    delay = policy.base_delay * (policy.backoff_factor ** attempt)
    delay = min(delay, policy.max_delay)
    return delay + random.uniform(0, policy.jitter)
```

**`extensions/middleware/retry.py`** — hardcoded values, inside `RetryMiddleware`:
```python
delay = min(
    self.base_delay * (2 ** attempt) + random.uniform(0, 1),
    self.max_delay,
)
```

`RetryMiddleware` hardcodes `backoff_factor=2` and `jitter=1.0`. The `AssistantAgent` and `_tool_execution.py` both already import the `RetryPolicy` + `_calculate_delay` path. The middleware is a weaker, non-configurable copy.

### Fix

1. In `RetryMiddleware.__init__`, accept an optional `RetryPolicy` parameter and fall back to a default-constructed one
2. Replace the inline backoff math in `RetryMiddleware` with a call to `_calculate_delay(attempt, self._policy)`
3. Mark the old inline fields (`base_delay`, `max_delay` on the middleware) as deprecated
4. Document that the canonical retry path is `RetryPolicy` + `retry_async` decorator; `RetryMiddleware` is middleware-pipeline sugar over the same logic

---

## Issue 3 — Six context strategies crammed into one misnamed file

### Problem

`extensions/context/redis_model_context.py` (520 LOC) contains six independent `ModelContext` implementations:

| Class | Lines | What it does |
|-------|-------|-------------|
| `UnboundedContext` | ~27 | Return all messages, no trimming |
| `SlidingWindowContext` | ~36 | Keep last N messages |
| `TokenBudgetContext` | ~51 | Keep messages up to token limit |
| `HybridContext` | ~101 | Fuse Redis hot store + Postgres cold store |
| `RedisModelContext` | ~65 | Strategy selector / orchestrator |
| `SummarizingContext` | ~149 | Summarise old messages via LLM |

The file name `redis_model_context.py` implies Redis-specific, but `UnboundedContext`, `SlidingWindowContext`, and `TokenBudgetContext` are pure in-memory strategies with no Redis dependency. A contributor looking for sliding-window trimming logic has no reason to open a file called `redis_model_context`.

### Fix

Split into one file per strategy under `extensions/context/`:

```
extensions/context/
  unbounded.py           ← UnboundedContext
  sliding_window.py      ← SlidingWindowContext
  token_budget.py        ← TokenBudgetContext
  hybrid.py              ← HybridContext (Redis + Postgres)
  summarizing.py         ← SummarizingContext
  redis_context.py       ← RedisModelContext (strategy orchestrator)
  __init__.py            ← re-export all six for backwards compat
```

Keep `redis_model_context.py` as a shim that imports and re-exports all six during a deprecation window if needed.

---

## Issue 4 — `ServerContext` name collides with `ModelContext`

### Problem

There are three different "context" concepts in the codebase, and two of them share the word "Context" in confusing proximity:

| Name | File | What it actually is |
|------|------|---------------------|
| `ModelContext` | `kernel/context/base_context.py` | ABC — strategy for building a message list to send to the LLM |
| `ExecutionContext` | `kernel/execution/context.py` | Dataclass — per-request state (thread_id, session_id, agent_id, flags) |
| `ServerContext` | `server/context.py` | DI container — holds `model_client`, `redis_memory`, `tools`, `bridge_registry`, and other app-level singletons |

`ServerContext` is not a context in any "ModelContext" or "ExecutionContext" sense — it is the FastAPI application state / dependency container. Its name causes new contributors to ask "what kind of context is this?" and check if it implements the kernel ABC (it does not).

### Fix

Rename `server/context.py` → `server/dependencies.py` and rename the class `ServerContext` → `ServerDependencies` (or `AppState`). Update all `from substrateserver.context import ServerContext` imports in routes and lifespan.

---

## Issue 5 — Tool parsing and approval logic duplicated

### Problem

Two separate files implement tool call normalisation and HITL approval:

**`extensions/agents/assistant/_tool_execution.py`**:
- `ParsedToolCall` dataclass — normalises heterogeneous tool-call shapes
- `parse_tool_call()` — converts `AssistantMessage.tool_calls` entries to `ParsedToolCall`
- `find_tool()` — looks up a tool by name in the registry
- `run_hitl_approval()` / `request_tool_approval()` — full HITL flow with timeout
- `execute_tool_direct()` — inline execution inside the agent loop
- `execute_tool_via_runtime()` — runtime-dispatched path

**`catalog/tools/_tool_executor.py`**:
- `ToolExecutorHandler` class — runtime message handler for tool execution
- Duplicates tool-name lookup and a simplified HITL check

Both files deal with the same problem: given a tool-call request (name + args), find the tool, optionally get human approval, run it, return a result. They diverge because `_tool_execution.py` was written for the agent loop and `_tool_executor.py` was written for the runtime message handler — but the shared concepts (parsing, lookup, approval) were never extracted.

### Fix

1. Create `kernel/tools/parsing.py`:
   ```python
   @dataclass
   class ParsedToolCall:
       id: str
       name: str
       arguments: dict[str, Any]

   def parse_tool_call(tc: Any) -> ParsedToolCall: ...
   def find_tool(name: str, registry: ...) -> BaseTool | None: ...
   ```
2. Create `kernel/tools/approval.py` with the `ToolApprovalHandler` Protocol and a `needs_approval()` predicate
3. Update both `_tool_execution.py` and `_tool_executor.py` to import from kernel instead of defining their own versions
4. The execution strategies (`execute_tool_direct` vs runtime dispatch) stay separate — that divergence is intentional

---

## Issue 6 — "Pipeline" is used for two unrelated concepts

### Problem

The word "pipeline" appears in two completely different contexts:

**`extensions/pipelines/`** — Agent workflow orchestration:
- `runner.py`: `PipelineRunner` — parses visual-builder JSON, instantiates agents/flows, detects topology (linear / branching / looping), returns a runnable
- `condition_runner.py`: `ConditionPipelineRunner` — branching via expression evaluation
- `while_runner.py`: `WhilePipelineRunner` — loop until condition
- These are about routing tasks through agent graphs

**`catalog/_pipeline.py`** — Data connector step execution:
- `PipelineEngine` — runs a sequence of `PipelineStep`s where each step is a connector (Postgres query, email send, HTTP call)
- Passes data between steps via `DataRef` substitution
- These are about ETL-style data flow, not agents

A contributor searching for "pipeline" finds both and has no immediate way to know which one handles what. The `PipelineRunner` in `extensions/` and the `PipelineEngine` in `catalog/` have no shared base class and cannot be substituted for each other, yet they share a name.

### Fix — two options (pick one)

**Option A (rename, lower risk)**: Rename `extensions/pipelines/` → `extensions/workflows/` and its classes accordingly: `WorkflowRunner`, `ConditionWorkflowRunner`, `LoopWorkflowRunner`. Leave `catalog/_pipeline.py` unchanged.

**Option B (unify, higher value)**: Create a `Pipeline` ABC in `kernel/`:
```python
class Pipeline(ABC):
    @abstractmethod
    async def run(self, input: Any) -> Any: ...
```
Have both `WorkflowRunner` and `PipelineEngine` implement it. This enables uniform composition and testing but requires more work.

Option A is lower-risk and sufficient for clarity. Option B is worth doing only if there is a concrete use case for treating agent workflows and data pipelines uniformly.

---

## Issue 7 — Lineage validation patterns duplicated (minor)

### Problem

`kernel/memory/_lineage.py` defines the `LineageStore` Protocol and the `LineageRecord` / `LineageSession` dataclasses. It also defines `_validate_session_id()` and `_validate_message_id()` helper functions with regex patterns.

`extensions/memory/_lineage.py` (`InMemoryLineageStore`) calls the kernel validators on ingestion. If any of the three integration backends (`integrations/memory/lineage_postgres.py`, `lineage_s3.py`) also import and call the same validators, the concern is already centralised — no duplication.

However, if the integration backends re-implement the validation regex independently (check during implementation), extract the patterns to `kernel/memory/_lineage.py` as module-level constants so all implementations share them:

```python
SESSION_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
MESSAGE_ID_PATTERN  = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
```

This is low priority — confirm first whether the backends actually re-implement it.

---

## What is already well-structured

These patterns are clean and should be preserved as-is:

- **Kernel boundary**: `kernel/` has zero concrete implementations; everything is ABCs, Protocols, or dataclasses. Import-linter enforces this.
- **Integration separation**: Each backend technology (Redis, Postgres, S3, OpenAI, Anthropic) has its own directory under `integrations/`.
- **Event fabric contracts**: `kernel/events/_fabric.py` correctly separates `DurableEventLog` and `RealtimeFanout` semantics. The issue is only that the concrete implementation ended up in the wrong layer (Issue 1).
- **Hook system**: `HookManager` + `BaseHook` lifecycle observability is clean and non-duplicated.
- **Guardrail runner**: `extensions/guardrails/runner.py` executes guardrails in parallel with OTel tracing — single implementation, no duplication.
- **Middleware pipeline**: `kernel/execution/pipeline.py` `ExecutionMiddlewarePipeline` is generic, type-safe, single implementation.
- **Plugin registry**: `@register_agent`, `@register_tool`, `@register_memory` decorators are centralised in `kernel/plugin/registry.py`.

---

## Prioritised work order

### Phase 1 — Layering and duplication (break things if left unfixed)

1. **Issue 1** — Move `shared/events/bus.py` to `integrations/`
2. **Issue 2** — Make `RetryMiddleware` delegate to `_calculate_delay()`
3. **Issue 5** — Extract `ParsedToolCall` + `parse_tool_call` to `kernel/tools/parsing.py`

### Phase 2 — Organisation (confusing but not dangerous)

4. **Issue 3** — Split `redis_model_context.py` into 6 files
5. **Issue 4** — Rename `ServerContext` → `ServerDependencies`
6. **Issue 6** — Rename `extensions/pipelines/` → `extensions/workflows/`

### Phase 3 — Validation hygiene (low priority)

7. **Issue 7** — Confirm lineage backends; extract constants if needed

---

## File inventory (relevant to this audit)

```
src/ravi/
├── shared/events/bus.py                         ← ISSUE 1: move to integrations/
├── extensions/middleware/retry.py               ← ISSUE 2: delegate to policies.py
├── extensions/resilience/policies.py            ← ISSUE 2: canonical retry logic
├── extensions/context/redis_model_context.py    ← ISSUE 3: split into 6 files
├── server/context.py                            ← ISSUE 4: rename ServerContext
├── extensions/agents/assistant/_tool_execution.py  ← ISSUE 5: extract ParsedToolCall
├── catalog/tools/_tool_executor.py              ← ISSUE 5: use kernel parsing
├── extensions/pipelines/runner.py               ← ISSUE 6: rename to workflows/
├── extensions/pipelines/condition_runner.py     ← ISSUE 6
├── extensions/pipelines/while_runner.py         ← ISSUE 6
├── catalog/_pipeline.py                         ← ISSUE 6: the other "pipeline"
├── kernel/memory/_lineage.py                    ← ISSUE 7: validation constants
└── extensions/memory/_lineage.py               ← ISSUE 7: verify reuse
```
