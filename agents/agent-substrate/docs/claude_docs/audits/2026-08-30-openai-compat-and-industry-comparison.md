# Audit — OpenAI-API compatibility, industry comparison, and code-clarity debt

Requested by the user: assess whether agent-substrate can genuinely serve
self-hosted models (vLLM/SGLang/llama.cpp) through an OpenAI-compatible
surface, compare against LangChain/LangGraph, Microsoft Agent Framework, and
Google ADK's 2026 direction, and judge whether the codebase reads as
deliberately-written software rather than accreted AI output. Pre-launch:
nothing here has a production user yet, so every finding below is a design
decision still open to change, not a regression to patch around.

All findings below are traced to a specific file/line or a specific external
source — nothing here is asserted from memory.

---

## 1. Correction (2026-08-30, same evening) — the original §1 here was wrong

The first version of this audit claimed `LLMFactory`'s `"compatible"`
provider built `OpenAIClient` (the Responses-API client) and was therefore
broken against self-hosted servers. **That was a real research error, not a
real bug** — it was written from an import-list grep
(`grep -rln "OpenAIChatCompletionClient" ...`) without actually reading
`LLMFactory.build()`'s dispatch logic. Reading `build()` directly
(`integrations/llm/factory.py:262-326`) shows the design was already
correct:

```python
if self._provider == "openai":
    return OpenAIClient(...)            # Responses API — real OpenAI cloud only

if self._provider in _CHAT_COMPLETIONS_PROVIDERS:   # compatible, vllm, ollama,
    return OpenAIChatCompletionClient(...)          # groq, lmstudio, etc.
```

`_CHAT_COMPLETIONS_PROVIDERS` (`factory.py:83-100`) already lists every
self-hosted-friendly provider, and the module's own docstring already
documents this split correctly. The two-clients-in-two-layers structure
noted in the original text is real and worth knowing (`OpenAIClient` in
`integrations/llm/openai/`, `OpenAIChatCompletionClient` in
`capabilities/llm/`) — but they are *not* in tension the way originally
claimed: `OpenAIClient` is the Responses-API client used only for real
OpenAI cloud; `OpenAIChatCompletionClient` is the Chat-Completions client
used for everything else, and the factory already routes correctly between
them.

**What was real and is now fixed:** zero tests verified this routing existed
at all — nothing would have caught a future regression breaking it.
Verified fresh (`tests/integrations/test_llm_factory_routing.py`,
`tests/capabilities/test_chat_client.py`, both passing): `LLMFactory`
correctly builds `OpenAIChatCompletionClient` for `compatible`/`vllm`/
`ollama`/`groq`, that client issues real `POST /v1/chat/completions`
requests (not `/v1/responses`), and a full tool-call round trip — the
mechanism `ReActAgent`'s tool loop depends on — works correctly against a
mocked self-hosted-shaped response.

**Net effect on the "harness works with self-hosted models" goal: already
true, now proven rather than assumed.** No routing fix was needed. The one
real, lasting finding from this section is the *process* lesson, not a code
lesson: an import-list grep is not evidence about dispatch logic — read the
function that actually decides, and this session's own established
practice (verify before asserting) should have applied to auditing this
codebase exactly as strictly as it applied to the GPU/throughput claims
made earlier the same night.

---

## 2. What "durable at minute pieces" actually means here, checked against real code

The user asked whether subagents and small tasks are handled durably, not
just the top-level run. Evidence, not assertion:

**Real strengths, verified in code:**
- `agents/runtime/worker.py:397-453` — a subagent crash is caught and turned
  into a typed `kernel.core.errors.AgentCrashError` (carrying `run_id`,
  `agent_id`), not swallowed or left to propagate as a bare exception.
- Supervision has a real advisory-lock-serialized spawn budget
  (`Supervisor.spawn()`), fixed on 2026-07-18 after a kernel audit found the
  documented enforcement point (`SupervisorProtocol.spawn()` raising a
  `SpawnDenied`) simply didn't exist in code — the only real check was a
  per-instance convention any direct `ctx.spawn()` caller could bypass. This
  is now closed, and the audit trail for the fix is in
  `docs/claude_docs/roadmap.md`'s "Recently shipped" section.
- Tool approval (a HITL suspend point mid-subagent-execution) is durable
  against a genuinely closed-and-reopened Postgres pool, not just a
  discarded in-process object — proven by
  `test_pg_tool_approval_durability_survives_full_pool_close_and_reopen`
  (`tests/agents/test_runtime_postgres.py`).
- `OrchestratorAgent` (`agents/core/orchestrator.py`) waits on each subagent
  via `ctx.ask(handle, boot_msg, timeout=cfg.ask_timeout)` with a real
  default timeout (120s) — a hung subagent does not hang the parent forever.

**Real gaps, self-documented by the project's own audits (not new findings —
cited so their status is visible in one place):**
- `InMemorySupervisor.cancel()` is immediate/forceful; `Supervisor.cancel()`
  (the Postgres-backed one) is cooperative with a ≤15s latency bound. A test
  suite that only exercises the in-memory backend cannot catch a regression
  in the real cooperative-cancel path — this divergence is flagged but
  unresolved (roadmap, 2026-07-18 entry).
- `ctx.tenant_id` is threaded through `RunMeta` correctly but **never read**
  by any tool or history/memory access inside a subagent's own execution —
  isolation is enforced only at the thread-ownership layer above a run, not
  inside one. Not an active leak (no call path was found constructing a
  colliding key), but a subagent's own code cannot assume tenant isolation
  is enforced where it runs.
- `SpawnTracker`'s cooperative-preemption half (`is_paused()`/
  `reprioritize()`) is real, tested bookkeeping — but no agent loop in the
  codebase actually consults it before an LLM call. A low-priority subagent
  cannot currently be paused mid-flight by a higher-priority one arriving;
  the mechanism to record the intent exists, the mechanism to act on it does
  not.
- Most of the pre-existing guardrail/infrastructure middleware family was
  found **unwireable in its current form** during the Phase 5 remediation
  (`ChatTracingMiddleware` was deleted rather than installed for this
  reason) — the middleware pipeline concept is real and tested
  (`MiddlewarePipeline`, `tests/agents/test_middleware.py`,
  `test_middleware_wiring.py`), but a chunk of the middleware classes that
  were meant to plug into it currently cannot.

**Net assessment:** the top-level run is genuinely durable (event-sourced,
replayable, survives a real Postgres restart, not just a process restart) —
this is further along than "just a tested package" suggests. The *inner*
loop — one subagent's own execution, priority preemption, tenant boundaries
inside a run — has real, already-known gaps. This matches the user's
instinct exactly: the framework is durable at the outer shell and thinner at
the minute pieces.

---

## 3. Where the industry moved in 2026, and where agent-substrate sits

Three converging frameworks were checked, each against a live source:

| Trend (2026) | LangChain/LangGraph | Microsoft Agent Framework | Google ADK | **agent-substrate** |
|---|---|---|---|---|
| Crash-survivable execution as a first-class guarantee | Landed as LangGraph's core 1.0 promise — "your agent should survive a server restart" ([LangChain blog](https://www.langchain.com/blog/langchain-langgraph-1dot0)) | Ships as managed Azure Durable Functions | — | **Ahead in rigor**: event-sourced core, replay-from-journal, `SuspendInterrupt`-based suspend/resume, proven against a real closed-and-reopened Postgres pool, not just process restart |
| Explicit split: LLM-driven orchestration vs. deterministic workflow orchestration | LangGraph is graph-based throughout, less of an explicit duality | Named directly: "Agent Orchestration" (creative/LLM-driven) vs. "Workflow Orchestration" (business-logic/deterministic) ([MS Foundry blog](https://devblogs.microsoft.com/foundry/introducing-microsoft-agent-framework-the-open-source-engine-for-agentic-ai-apps/)) | — | **Already has both**: `agents/core/orchestrator.py` (LLM-driven `OrchestratorAgent`) and `fabric/flows/` (deterministic `SequentialFlow`/`ParallelFlow`/`ConditionalFlow`) as separate, named concepts |
| MCP as a standard tool protocol | Yes | Native | — | Has it (`integrations/tools/mcp/`, `MCPClient`, `MCPTool`) — on par |
| **A2A (Agent2Agent) protocol** for cross-agent/cross-vendor interop | — | **Native support** | **Origin project; now Linux Foundation-governed, 150+ orgs in production** ([n1n.ai](https://explore.n1n.ai/blog/google-adk-1-0-a2a-protocol-multi-agent-standard-2026-05-04)) | **Not implemented.** `CLAUDE.md` lists it as "planned"; only a bare mention exists in `kernel/tools/tools.py`, no protocol client/server |
| **AG-UI** (standardized agent↔frontend streaming protocol) | Ecosystem-adjacent | Native support | — | **Not implemented.** `serving/protocol/`'s `WireEvent` union is a bespoke SSE schema — works, but is not interoperable with any external AG-UI-speaking frontend without a translation layer |
| Middleware as a first-class extension point | LangChain 1.0 ships this as a named system (HITL checkpoints, summarization, PII redaction as prebuilt middleware) ([LangChain blog](https://www.langchain.com/blog/langchain-langgraph-1dot0)) | Native | — | Has the mechanism (`MiddlewarePipeline`) but, per §2, a chunk of the concrete middleware classes meant to use it are currently unwireable |
| Unified single-abstraction agent across model providers | `create_agent` now runs on LangGraph's runtime | `AIAgent` abstraction, one API across providers | — | Has this at the `LLMClient` Protocol level (`kernel/llm/llm.py`) — `LLMFactory` already dispatches self-hosted providers correctly (§1), now with tests proving it |
| OpenAPI-based tool/agent definitions | — | Native OpenAPI agent support | A2A 0.2 adds OpenAPI-like auth schema | Tool schemas are hand-defined JSON Schema per tool (`Tool.input_schema`) — functional, but no OpenAPI-spec-to-tool generation path |

**Reading this table plainly:** agent-substrate's core execution model
(durability, event-sourcing, the orchestration/workflow duality) is not
behind — in the durability dimension specifically, the rigor documented in
this repo's own audits exceeds what the public blog posts above claim for
LangGraph. Talking to self-hosted model servers already works (§1). What's
missing is **protocol standardization at the two remaining edges**: A2A for
talking to *other* agent frameworks, and AG-UI for talking to *other*
frontends. Both gaps share the same shape: the framework is strong internally
and closed at these two specific boundaries. That is a coherent, useful
finding — these boundaries are where a pre-launch project should invest next
if interoperability with other agent ecosystems and third-party UIs is a
stated goal, which it is here.

---

## 4. "Reads like human-written software" — a few concrete, checkable examples

This is inherently judgment-based, so it's kept to things that are directly
verifiable rather than a vibe:

**Good signs, found while reading for this audit:**
- The roadmap in `docs/claude_docs/roadmap.md` is unusually honest for a
  project of this kind — it records things that were *claimed* to work and
  turned out not to (`SpawnBudget` enforcement, tool-approval wiring, the
  `ChatTracingMiddleware` deletion) rather than only listing what shipped.
  That habit — writing down "we thought X was true, it wasn't, here's the
  fix" — is a strong, unusual signal of engineers rather than a code
  generator narrating success.
- The kernel/agents/capabilities/fabric layering is enforced by
  `import-linter` contracts, not just a docstring convention — a real,
  CI-checked architectural boundary (`uv run lint-imports`, 4 contracts, all
  currently green per this session's own `pyproject.toml` check).
- Comments in the code consistently cite *why*, with evidence
  ("real, found-not-assumed", measured numbers, a specific failing test)
  rather than restating *what* the code does — this matches the pattern this
  session itself used all night on `pdfqa-rag`/`agent-substrate`, and it
  reads the same way in code the user didn't write this session.

**Signs of exactly the "AI spaghetti" pattern the user is worried about** —
real ones, found elsewhere, since §1 turned out not to be one:
- The roadmap itself lists several near-duplicates found and killed in past
  audits (`RetryPolicy` dead code duplicating `RunRetryPolicy`; three
  independently-built tool-approval abstractions found not wired to a real
  agent at all). The project has a demonstrated pattern of accreting a
  second implementation next to a first, then having to discover and
  reconcile it later. Two OpenAI-shaped clients living in two different
  layers (`integrations/llm/openai/` vs `capabilities/llm/`) is structurally
  the same shape as those past duplicates, even though in this case the
  factory routes between them correctly — worth a name-and-placement pass
  later (should a Responses-API client and a Chat-Completions client really
  sit in two different architectural layers?) even though it isn't a bug
  today.
- This audit's own first draft is itself an instance of the more general
  risk: a plausible-sounding finding, asserted from an incomplete check
  (an import grep instead of reading the dispatch function), that would
  have stood as fact in a committed file if not re-verified. The fix wasn't
  "write more carefully" — it was "run the test before trusting the claim,"
  the same discipline the rest of this session already enforced everywhere
  else.

---

## 5. Recommended next steps, roughly in order

1. **§1 needed no fix — done.** Self-hosted-server interoperability
   (vLLM/SGLang/llama.cpp) already worked via `LLMFactory`; it now also has
   test coverage proving it (`tests/integrations/test_llm_factory_routing.py`,
   `tests/capabilities/test_chat_client.py`, both committed 2026-08-30).
2. **Decide on A2A.** Given it's now Linux-Foundation-governed with 150+
   production adopters and is native in the Microsoft framework, treat this
   as a real roadmap item, not a "someday." Scope: a client for calling out
   to other A2A agents first (smaller, immediately useful for the RAG/agent
   work already underway), server-side A2A exposure second.
3. **Decide on AG-UI**, specifically whether `serving/protocol/`'s bespoke
   `WireEvent` schema should be replaced or translated to AG-UI. This is a
   bigger, riskier change (it's load-bearing for the existing SSE streaming
   path) — worth a dedicated design pass, not a quick patch.
4. **Reconcile the middleware family** flagged as unwireable in the Phase 5
   remediation — either fix the concrete classes to match
   `MiddlewarePipeline`'s real interface, or delete them the way
   `ChatTracingMiddleware` was, so the "middleware" story isn't half-real.
5. **Close the two known durability gaps in §2** (cooperative-cancel parity
   between backends; `SpawnTracker` preemption actually consulted by the
   agent loop) before treating subagent orchestration as production-grade.

The remaining real gap from the two things the user named is narrower than
the first draft of this audit claimed: self-hosted-model interoperability
is done (§1); being confidently "durable at minute pieces" under real
multi-agent load still has the known-but-open gaps in §2.
