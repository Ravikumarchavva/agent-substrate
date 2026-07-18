---
hide:
    - navigation
---
# Capability Map

**What the platform provides, organized by concern rather than by code layer.** Every item below names a real, shipped class — nothing aspirational.

---

## The mental model — three tiers, strictly separated

<div class="grid cards" markdown>

-   :material-memory: **Context — RAM**

    ---

    The working set the model sees *this turn*. Assembled fresh every step: the transcript is compacted into a prompt window that fits the context limit. Ephemeral by design — rebuilt, not stored.

-   :material-brain: **Memory — Knowledge**

    ---

    What the agent *knows* — session state and durable facts. A cognitive concern with pluggable retrieval: key-value, full-text, vector, graph, or reasoning-based page index.

-   :material-database-outline: **Storage — Disk**

    ---

    The persistence mechanisms underneath — Postgres, Redis, pgvector, Apache AGE, and optional MinIO / S3 object storage. Memory and RAG plug in on top; agents never touch these directly.

</div>

---

## Context — how the prompt window is built

`HistoryProvider` (full transcript — `CachedHistoryProvider` in production: a fast Redis cache that self-heals from the EventLog on a cold session) → `CompactionStrategy` (sliding window · token budget · summarization · tool-result pruning) → the prompt window the model actually sees this turn.

---

## Memory — two scopes × pluggable backends

| Scope | Shipped backend | Retrieval styles |
|---|---|---|
| **Short-term** (`ShortTermMemory`)<br>*one session* | `CachedShortTermMemory` — durable-first: writes land in Postgres JSONB, then the Redis cache; reads check Redis first, fall back to Postgres on a miss | key-value session state |
| **Long-term** (`LongTermMemory`)<br>*across sessions* | `DurableMemoryStore` | full-text search · semantic (vector) · entity graph · reasoning-based page-index navigation |

Default construction, one call:

```python
# Postgres only, no cache
stm = await build_short_term_memory(database_url)

# Postgres + Redis cache in front of it
stm = await build_short_term_memory(database_url, redis_url=redis_url)

ltm = await build_long_term_memory(database_url)
```

`build_long_term_memory` has no cache variant — `search()` is arbitrary-query, which a key-value cache doesn't fit the way flat session state does.

---

## Capabilities by concern

<div class="grid cards" markdown>

-   :material-robot-outline: **Agent Core** — `AGENTS · FABRIC`

    ---

    Production ReAct loop plus multi-agent coordination primitives that compose recursively.

    `ReActAgent` · `OrchestratorAgent` · `Runtime` · `SequentialFlow` · `ParallelFlow` · `ConditionalFlow`

-   :material-memory: **Context** — `AGENTS`

    ---

    The RAM tier — history providers feed compaction strategies that produce the per-turn prompt window.

    `ContextConfig` · `CachedHistoryProvider` · `InMemoryHistoryProvider` · `RedisHistoryProvider` · `DurableHistoryProvider` · `SlidingWindowCompaction` · `SummarizationCompaction` · `TokenBudgetComposedStrategy` · `CompactionPipeline`

-   :material-brain: **Memory & Knowledge** — `CAPABILITIES`

    ---

    Session state, durable facts, and document knowledge — each behind a protocol with swappable retrieval.

    `CachedShortTermMemory` · `DurableSessionStore` · `RedisSessionStore` · `DurableMemoryStore` · `RAGPipeline` · `GraphRAGPipeline` · PageIndex RAG

-   :material-database-outline: **Storage** — `INFRASTRUCTURE`

    ---

    The disk tier. Mechanisms only — higher concerns plug in through kernel protocols, never directly.

    `Postgres` · `Redis` · `PgVectorStore` · `AGEGraphStore` · `PgTaskStore` · `MinIOConnector` *(optional S3)*

-   :material-shield-alert-outline: **Guardrails** — `AGENTS`

    ---

    Async tripwire middleware evaluating inputs, outputs, and tool calls — trip cleanly with `status="guardrail_tripped"`.

    `ContentFilterMiddleware` · `PromptInjectionMiddleware` · `PIIDetectionMiddleware` · `LLMJudgeMiddleware` · `MaxTokenMiddleware` · `ToolCallValidationMiddleware`

-   :material-gavel: **Governance & HITL** — `KERNEL · AGENTS`

    ---

    Regulates draw, not data: spend ceilings, spawn caps, and human approval on risky tool calls — waits survive restarts.

    `SpawnBudget` · `ExecutionBudget` · `ToolRisk` tiers · `ApprovalHandler` · `Priority` · `HistoryRetention`

-   :material-checkbox-marked-circle-outline: **Evals** — `FABRIC`

    ---

    Dataset-driven evaluation with LLM-as-judge scoring and structured reports, runnable in CI.

    `EvalDataset` · `EvalCriterion` · `LLMJudge` · `EvalRunner` · `EvalReport`

-   :material-chart-line: **Observability** — `INFRA · SERVING`

    ---

    Every run is traced and journaled — inspect a live agent or replay a dead one.

    OpenTelemetry traces · runtime metrics · `ObservabilityMiddleware` · EventLog journal · Grafana + Tempo

-   :material-toolbox-outline: **Tools & MCP** — `CAPABILITIES`

    ---

    JSON-schema-validated tools with risk-tiered approval, sandboxed code-mode chaining, and MCP auto-discovery.

    `ToolRegistry` · `MCPClient` / `MCPTool` · `ToolChainTool` · `CalculatorTool` · `WebSearchTool` · `WebSurferTool` · + skills

-   :material-swap-horizontal: **Model Clients** — `INTEGRATIONS`

    ---

    Provider auto-detected from the model name. Swap brands without touching agent code.

    `LLMFactory` · OpenAI · Anthropic · Gemini · Groq · Ollama · `EmbeddingClient`

    e.g. `LLMFactory("claude-opus-4-8", key).build()`

</div>

---

Contracts for every concern live in the frozen kernel (L0) — see the [Kernel Board](kernel-board.html) for the contract-level view, or the [Agent Builder](agent-builder.html) to generate working construction code.
