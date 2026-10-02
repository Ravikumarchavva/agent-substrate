# The kernel — the engine

`substrate.kernel` is the engine: everything needed to run a durable agent. It is not a
thin layer of contracts under a separate runtime; it does the work. What it needs from the
outside world is declared in `kernel/abstractions`, and everything that touches the outside
world — vendors, databases, HTTP — sits above it.

```
kernel/abstractions   ports + value types. Pure: stdlib + pydantic. What an adapter implements.
kernel/               the engine, built on those ports
  agents/             RoutedAgent (+ @handle), ReActAgent, OrchestratorAgent, UserProxyAgent, flows' base
  runtime/            Runtime, Worker, Journal, RunContext, SqlRuntimeStore (+ SQLite adapter)
  telemetry/          spans and metrics at every chokepoint (opentelemetry-api only)
  llm/                capability registry, modality fitting, error classification
  context/ tools/ middleware/ limits/ safety/ storage/ workspace/ flows/ document/
  testing/            conformance suites and doubles; never imported by production code
evals/                the eval harness, a client of the kernel (not part of it)
integrations/ runtimes/   adapters: vendor LLM clients, Postgres/Redis/S3/Lance stores, MCP, tools
serving/ console/ cli     wiring: FastAPI apps, the REPL, the CLI
```

Enforced by `uv run lint-imports` (four contracts) and `tests/invariants/test_structure.py`:
the kernel imports nothing above it; its only third-party imports are pydantic,
`opentelemetry-api`, `confusable_homoglyphs` and `typing_extensions`; `abstractions` never
imports the engine; adapters use `abstractions` and the engine's named support libraries
(see the contract's comment in `pyproject.toml`), never `agents/context/flows/middleware/limits`.

## Running an agent

```python
async with Runtime.local("./data/runtime.sqlite3") as rt:      # or Runtime(PostgresRuntimeStore(dsn))
    await rt.register(agent)
    run_id = await rt.submit(agent.id, message, thread_id=..., tenant=...)
    async for entry in rt.tail(run_id): ...                     # live output + the durable record
```

One engine, one port. `Runtime` owns a `Worker` that leases runs from a `RuntimeStore`; the
store is the only thing that varies (SQLite file, Postgres). Every engine behaviour — leases,
retries, replay, supervision — lives in the engine, not in a store, so it is identical across them.

`RuntimeStore` (`abstractions/runtime/store.py`) is command-oriented: each method is one
transaction and the engine owns policy. `commit(lease, Commit(entries, ack, nack, deliveries,
signals, spawns, outcome))` is the unit of atomicity: journal entries, inbox acknowledgement,
child spawns, the parent's wake-up and the run's new status land together or not at all. The
lease **epoch** fences every command (`LeaseLostError`).

## Durable execution (the part to get right)

* **Identity** of a journaled step is `(run_id, hierarchical path, kind)`; arguments are stored as
  a digest, so a replay that meets a different kind or different arguments at a path raises
  `NonDeterminismError` before any side effect.
* **Intent before effect.** `effect.intent` is committed before a tool runs, `effect.result` after.
  A replay sees three states: completed (serve it), absent (run it), orphaned (intent, no result).
  An orphaned effect re-runs under the same idempotency key if the tool declared `idempotent`,
  otherwise the run fails with `OrphanedEffectError` and the intent is the record to compensate from.
* **Atomic steps** (send, spawn, now, uuid, random, drain) are themselves one commit.
* **A run is pinned to the agent version that started it** (recorded in its own journal); a worker
  with a different version refuses it rather than attach old results to new code.
* **Live tokens are ephemeral entries**: visible to a tail, never to a replay, dropped after the run.
  The durable record holds one `assistant.message` per model call.
* **Budgets are tree-wide.** Spend is added to the execution tree's total in the same commit as the
  `llm.call` entry that paid for it (dedup-guarded, so a replay never double-counts) and checked
  before and after every model call: total spend exceeds a cap only by calls already in flight.

## The invariant register

`docs/claude_docs/architecture/invariants.md` is **generated** from the tests in
`tests/invariants/` (`uv run python -m tests.invariants.register`); a test fails if it is stale.
Each row is a guarantee that is executed, tagged by how it is enforced: type, constructor,
runtime check, conformance suite (every implementation), crash matrix, property test, architecture
test. The crash matrix (`tests/invariants/_harness/crash.py`) fails every durable write of a scenario
once, as a recoverable error and as a killed worker, and asserts the guarantees after recovery.
A guarantee not yet true is `xfail(strict=True)` naming what fixes it.

Conformance suites live in `kernel/testing/conformance/` and are run by every implementation of
their port: `RuntimeStore` (SQLite, Postgres), `MemoryStore` (local, Postgres, Lance), `VectorStore` (local,
pgvector, LanceDB), `HistoryProvider` (local, Postgres), `ObjectStore` (workspace folder, S3), `TaskStore` (local,
Postgres), `GraphStore` (local, Lance), `LLMClient` (OpenAI chat + Responses, Anthropic, Gemini — through each vendor's real SDK over a
scripted HTTP transport), `EmbeddingClient` (OpenAI, Gemini, local sentence-transformers, embedding-reranker service),
`DocumentExtractor` (local, document-intelligence service). There is no in-memory store: the minimum a durable agent rests on is a folder.
Row I30 fails the build if an implementation of a port that has a suite does not run it.

## Safety, tenancy, observability

* **Tools declare `risk` (a `ToolRisk`) and `idempotent` (a bool)**; `Toolbox.add` refuses a tool
  that does not. Tools from an MCP server default to `HIGH` / not idempotent unless the operator who
  connected the server says otherwise.
* **Memory is scope-addressed.** A record is owned by a `MemoryNamespace`; a caller sees records whose
  owner fields it shares (omitting a field narrows, never widens). Reads and deletes name the caller —
  an id alone addresses nothing — ids are tenant-qualified, taking over another namespace's id raises
  `ScopeViolationError`, and cross-user reads need an explicit `TenantWide(reason=…)`. `erase(within)`
  removes everything under a tenant/user/agent/session, and the GDPR eraser reaches memory and the run journal.
* **Every store port has a scope-bound handle.** `bind_history/vector/graph/objects/tasks(store, scope)`
  (`kernel/storage/scoped.py`, written once over the ports so it holds for every implementation) places each
  session, collection, conversation, key and namespace under the tenant (percent-encoded, so no name can look like
  another tenant's), refuses ids that resolve elsewhere, and rejects object keys that could climb out. `fence_objects` keeps serving's absolute `tenants/<t>/...` keys but refuses anything outside the tenant or containing `..`;
  request code reaches objects only via `ctx.files_for(tenant)` / `ctx.pending_for(tenant)`. Serving binds conversation history (agent
  build, scheduled runs, branch/checkpoint routes) and task boards (`TaskManagerTool.store_for(tenant)`, task routes);
  a run gets its scope from `ctx.store_scope`; there is no unscoped handle to forget to scope. Rows I1–I3 in `test_scope_binding.py`
  run the conformance suites *through* a bound handle and then attack the wall.
* **Identity travels on `ctx.scope`** (`RunScope`), stamped from authenticated transport input;
  tools read it, never a model-supplied argument.
* **Provider outcomes are typed:** every client reports a `FinishReason`; `classify_llm_error` yields
  `RateLimitedError(retry_after)`, `ContextLengthError` (the agent halves the prompt and retries once),
  `ContentFilterError`, `AuthError`; a reply cut off at the token limit is marked `run.truncated`.
* **Approvals are attributable:** the decider and time are stamped server-side and journaled as
  `approval.decided`.
* **Telemetry** is one mechanism (`kernel/telemetry`): a run span (parent = the context persisted at
  submission, so a trace survives leases, children and replays), handler, LLM and tool spans, GenAI
  + `substrate.*` conventions, content attributes dropped unless `SUBSTRATE_CAPTURE_CONTENT` is set.

## Not done yet

Nothing is pending in the register (95 enforced, 0 pending). What remains is outside the kernel: the plugin registry and
host package, the `runtimes/` restructure, and the distribution split (see the plan in `decisions.md`).
