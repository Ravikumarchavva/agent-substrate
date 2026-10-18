# The core — the engine

`substrate` is the engine: everything needed to run a durable agent. It is not a
thin layer of contracts under a separate runtime; it does the work. What it needs from the
outside world is declared by contracts inside each concept (`protocols.py`, the store contracts), and
everything that touches the outside world — vendors, databases, HTTP — sits above it.

One folder per concept, the contracts beside their built-in implementation, ordered bottom-up. A concept imports
only the ones before it:

```
types        content blocks, messages, ids, usage, errors, run scope, supervision/budgets, run log
telemetry    spans and metrics at every chokepoint (opentelemetry-api only)
tools        Tool contracts, approval, chaining policy, skills, Toolbox
models       ChatModel/EmbeddingModel, capability registry, modality fitting, error classification
stores       History/Memory/Vector/Graph/Object/Task contracts; local/ = the folder implementations; scoped.py = tenant binding
documents    DocumentExtractor/Chunker contracts, local document store
workspace    branching, snapshots, content-addressed files
safety       classifier contracts, text normalisation
context      the context window, compaction, history (the linear view of a thread)
middleware   Middleware contract, built-ins, guardrails
runtime      RuntimeStore, Runtime, Worker, Journal, RunContext, SQLite adapter
agents       RoutedAgent (+ @handle), ReActAgent, OrchestratorAgent, UserProxyAgent, flows, spawn limits
testing      conformance suites and doubles; never imported by production code
-------
evals/                the eval harness, a client of the core (not part of it)
integrations/             adapters: vendor LLM clients, Postgres/Redis/S3 backends, MCP, tools, and URL clients for the heavy services
apps/ packages/           the heavy services (document intelligence, embedding/reranking, the inference pool): projects of their own
serving/ console/ cli     wiring: FastAPI apps, the REPL, the CLI
```

Enforced by `uv run lint-imports` (four contracts) and `tests/invariants/test_structure.py`:
the core imports nothing outside itself and only imports downward; its only third-party imports are pydantic,
`opentelemetry-api`, `confusable_homoglyphs` and `typing_extensions`; the contracts (`tests/_layout.py`) never
import the engine; adapters use the contracts and the engine's named support libraries
(see the contract's comment in `pyproject.toml`), never `agents`, the context window internals or middleware implementations.
Import a public name from its concept package (`from substrate.runtime import Runtime`).

## The store

The engine's state is one thing you point at, as with Chroma's persist directory or a Lance database — the library
owns what is inside:

```python
store = await substrate.connect("./.substrate")     # or Store.at(folder) and `async with`
runtime = Runtime(store)                            # Runtime.open(folder) opens (and closes) one of its own
```

```
.substrate/
  substrate.db   the source of truth: runs + journal, threads (the conversation DAG), long-term memory (+ full-text index), session state, task boards, the knowledge graph (recursive-query traversal), vectors (exact + full-text + hybrid search), file names + metadata, workspace snapshots, the library catalog (document sections, full-text index)
  files/         file contents — written whole and flushed to disk before the row that names them commits; never modified in place (copying a prefix is a hard link per file); `Store.files.collect_garbage()` removes what a crash left unreferenced
  index/         indexes derived from substrate.db; deleting them loses nothing, they are rebuilt
```

`substrate.db` is a relational database (`stores/database.py`: `Database`, `Tx`, `migrate`) opened with a write-ahead
log and `synchronous=FULL`, so a transaction that returned survives a kill (row: the store's `test_a_committed_transaction_survives…`).
Each part of the engine owns its tables and ships them as ordered migrations recorded in the database; a folder from a
newer build is refused (`StoreVersionError`). Every part lives in the same database, so a later step can make one turn commit as one transaction — today each part commits on its own. Workers on one host share the folder. For workers on several machines the same engine runs over PostgreSQL —
`integrations.database.postgres_store(dsn)` (a `Database` adapter; its own schema, `substrate` by default) — with the rows in Postgres
and file contents in the `files` folder (or an `S3FileStore`). The few things SQL spells differently live in one place
(`stores/textsearch.py`: FTS5 + triggers on SQLite, a generated `tsvector` + GIN on Postgres; the vector column: packed bytes
on SQLite, a pgvector column with an HNSW index per vector width — `halfvec` above 2,000 dimensions — on Postgres).
Every facet runs the same conformance suite on both (`tests/integrations/test_postgres_store_conformance.py`). There is no in-memory store: tests use a folder (`ephemeral_runtime`, `runtime_store`).

## Running an agent

```python
async with Runtime.open("./.substrate") as rt:      # a folder; Runtime(store) to share a connected Store
    await rt.register(agent)
    run_id = await rt.submit(agent.id, message, thread_id=..., tenant=...)
    async for entry in rt.tail(run_id): ...                     # live output + the durable record
```

One engine, one port. `Runtime` owns a `Worker` that leases runs from a `RuntimeStore`; the
store is the only thing that varies (the folder store, later Postgres). Every engine behaviour — leases,
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

Conformance suites live in `testing/conformance/` and are run by every implementation of
their port: `RuntimeStore`, `MemoryStore`, `VectorStore` (`SearchableVectorStoreConformance` adds word and hybrid search), `ThreadStore`,
`TaskStore`, `GraphStore`, `ShortTermMemory` and `WorkspaceStore` (each on the folder store and on Postgres), `FileStore` (folder, Postgres, S3), `ChatModel` (OpenAI chat + Responses, Anthropic, Gemini — through each vendor's real SDK over a
scripted HTTP transport), `EmbeddingModel` (OpenAI, Gemini, local sentence-transformers, embedding-reranker service),
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
* **`store.tenant(t)` is the store as one tenant sees it** (`stores/tenant.py`): threads, tasks, vectors, graph and files
  confined to `t`, and `erase()` removes everything the tenant stored — thread history, task boards, vectors, graph, files and memory
  — leaving the words in neither the search indexes nor the database file (`erase_conversation(id)` for one conversation; the GDPR eraser
  calls both). It is built from `bind_threads/vector/graph/tasks(store, scope)` (`stores/scoped.py`, written once over the ports so it
  holds for every implementation, and public for ports that are not a folder store) places each
  session, collection, conversation, key and namespace under the tenant (percent-encoded, so no name can look like
  another tenant's), refuses ids that resolve elsewhere, and `fence_objects` keeps serving's absolute `tenants/<t>/...` keys but refuses anything outside the tenant or containing `..`;
  request code reaches objects only via `ctx.files_for(tenant)` / `ctx.pending_for(tenant)`. Serving binds conversation history (agent
  build, scheduled runs, branch/checkpoint routes) and task boards (`TaskManagerTool.store_for(tenant)`, task routes);
  a run gets its scope from `ctx.store_scope`; there is no unscoped handle to forget to scope. Rows I1–I3 in `test_scope_binding.py`
  run the conformance suites *through* a tenant view and then attack the wall; rows I4 there erase a tenant and check no other tenant lost a row.
* **Identity travels on `ctx.scope`** (`RunScope`), stamped from authenticated transport input;
  tools read it, never a model-supplied argument.
* **Provider outcomes are typed:** every client reports a `FinishReason`; `classify_llm_error` yields
  `RateLimitedError(retry_after)`, `ContextLengthError` (the agent halves the prompt and retries once),
  `ContentFilterError`, `AuthError`; a reply cut off at the token limit is marked `run.truncated`.
* **Approvals are attributable:** the decider and time are stamped server-side and journaled as
  `approval.decided`.
* **Telemetry** is one mechanism (`telemetry/`): a run span (parent = the context persisted at
  submission, so a trace survives leases, children and replays), handler, LLM and tool spans, GenAI
  + `substrate.*` conventions, content attributes dropped unless `SUBSTRATE_CAPTURE_CONTENT` is set.

## Not done yet

Nothing is pending in the register. What remains is the shape of the rest of the library — see the plan:
interface renames (`ChatModel`→`ChatModel`, `ThreadStore`→`ThreadStore`, …), library hygiene (core dependencies,
logging), the server package, and the split of integrations into packages.
