# Kernel Production-Readiness Audit

**Scope**: `ravi-engine/src/ravi/kernel/` (17 files, ~2,000 LOC)
**Date**: 2026-06-11
**Status**: Findings incorporated in kernel v2 remediation

---

## A. Dependency Direction

**A1 (Medium) — Docstring coupling to upper layers.**
Kernel semantics documented in terms of concrete upper-layer classes:
- `protocol.py:3-5` references LocalRuntime, agents/runtime
- `skills.py:13` references deleted ReActAgent
- `content.py:14` says "Provider encoders in fabric/" (they live in integrations/)
- `errors.py:25-27` references RetryPolicy and `agent.run(resume=True)`
- `identity.py:111` references SpawnBudget
- `memory.py:118-121` names PostgresMemoryStore etc.

When upper layers rename the frozen contract's documented meaning rots.
**Fix**: Rewrite all docstrings in terms of roles, not concrete classes.

**A2 (High) — Vendor wire-formats embedded in L0.**
- `tools.py:137-155` `Toolbox.deferred_schemas()` hardcodes OpenAI `defer_loading`, `tool_search`, "gpt-5.4+"
- `Toolbox.schemas()` emits the OpenAI function-calling shape
- `content.py:129` `ImageBlock.detail` is an OpenAI vision parameter

Dependency direction inverted: the foundation encodes one vendor's API.
**Fix**: Schema shaping moves to `integrations/llm` encoders; kernel exposes only `name/description/input_schema`.

---

## B. Public API Design

**B1 (Critical) — `Message.payload: object`** (`message.py:118`)
The central communication unit is untyped and non-serializable as a whole.
Proven damage in-repo:
- `capabilities/history/redis_history.py:33-51` isinstance-sniffs payloads; falls back to `payload_type="unknown"` with an empty dict → **silent data loss**
- `postgres_history.py:243-245` does incompatible `hasattr(payload, "model_dump")` dance
- Every future transport (Kafka, NATS, Temporal) must re-invent serialization
**Fix**: Typed `Payload` discriminated union + public registry.

**B2 (Critical) — `LLMClient` protocol doesn't match any implementation** (`llm.py:26-46`)
- `generate_stream(messages, **kwargs)` omits `tools`/`system_instructions` entirely
- All 6 implementations need them but flow through `**kwargs: object`
- Protocol says `async def generate_stream(...) -> AsyncIterator[...]` (caller must await)
- All 6 implementations are async generators consumed without await
- `react.py:992` has a `_stream_generate` helper with `inspect.isawaitable` to paper over the mismatch
**Fix**: `GenerationOptions` dataclass; `def generate_stream` (non-async) returning `AsyncIterator`; tools/system_instructions first-class.

**B3 (High) — Duplicate tool-call representations.**
- `ToolUseBlock`/`ToolResultBlock` in `content.py:226-257`
- `ToolCallRequest`/`ToolExecutionResult` in `message.py:34-68`
Same two concepts modelled twice. `ToolResultBlock` lacks `name`; `ToolExecutionResult.metadata`/`structured_content` are lost when lowering. Agent loops carry conversion code; the two halves drift.
**Fix**: One canonical pair + explicit lowering helpers.

**B4 (High) — Silent corruption fallback.** (`content.py:395-410`)
`content_block_from_dict` converts unknown/invalid blocks to `TextBlock(text=str(data))`.
In mixed-version distributed deployments a `tool_use` block from a newer node becomes garbage text on an older node with only a log warning.
**Fix**: Raise typed `BlockValidationError`; preserve `UnknownBlock` for lossless round-trip.

**B5 (Medium) — `ToolExecutionResult` config outlier** (`message.py:62`)
Only mutable, `arbitrary_types_allowed` model in the kernel — invites non-serializable content into a wire type.

**B6 (Low) — Export drift** (`__init__.py`)
`ToolType` (used by 4+ modules) and `EmbeddingResult` not exported. `Subscription` exported but no kernel API produces or consumes it (`AgentRuntime.subscribe` returns `None`).

**B7 (Medium) — Presentation concerns in L0.**
`ToolUI` carries CSP domain lists, iframe sandbox permissions, border hints. `UIResourceBlock` documents postMessage channels. The kernel should treat UI resources as opaque.

---

## C. Extensibility

**C1 (High) — ContentBlock union closed in four places** (`content.py:333`, `:357`, `:379`, `__init__.py`)
No downstream package can add a block type without editing the kernel. The registry `_BLOCK_REGISTRY` is private and the union is sealed.
**Fix**: Public `register_block_type()` + `UnknownBlock` escape hatch.

**C2 (Medium) — `Toolbox` is a concrete class in the contracts layer** (`tools.py:93-165`)
Violates the kernel's own invariant ("Protocols, dataclasses, enums"). Consumers coupled to one in-memory dict registry.
**Fix**: `ToolRegistry` Protocol in kernel; move `Toolbox` to agents layer.

**C3 (Medium) — `GraphStore.query_cypher`** (`graph.py:44-47`)
Bakes a query language into the universal contract; Gremlin/SPARQL stores cannot implement it.
**Fix**: Optional `CypherCapable` protocol. Core contract stays language-agnostic.

**C4 (Medium) — `VectorStore.add` parallel lists** (`vector.py:32-38`)
`documents` + `embeddings` must be index-aligned. Stores with server-side embedding cannot comply.
**Fix**: Optional `embedding` field on `Document`.

**C5 (High) — History API cannot implement its own retention contract.**
`HistoryRetention.RUN` promises run-scoped deletion but `HistoryProvider` keys only by `(agent_id, session_id)` — no `run_id`. "Delete after run" requires deleting whole-session history. Contradicts approved supervision-v2 design.
**Fix**: Add `run_id` to `append`/`get_messages`/`clear_run`.

---

## D. Interface Design

**D1 (High) — Middleware protocols are a fiction** (`middleware.py`)
TypeVars `_AgentCtxT/_ChatCtxT/_FuncCtxT` (lines 21-23) are **unused** — all three protocols are structurally identical `process(context: Any, call_next)`. Any middleware satisfies all three; a `FunctionMiddleware` type-checks as `AgentMiddleware`.
**Fix**: One generic `Middleware(Protocol[CtxT])` with per-level minimal context protocols.

**D2 (Medium) — `AgentStep` is a constants class, not an enum** (`stream.py:79-89`)
Unlike `ToolRisk`/`Priority`/`HistoryRetention`. `AgentProgress.step: str` accepts anything.
**Fix**: `AgentStep` → `StrEnum`.

**D3 (High) — No `Agent` contract.**
Agents are anonymous `MessageHandler` closures. Distributed runtimes must instantiate, host, checkpoint, and migrate agents — none expressible without an `Agent` protocol.
**Fix**: `Agent` protocol with `id`, `bind`, `on_message`, `save_state`, `load_state`.

**D4 (Low) — `EmbeddingClient.embed_single`** is derivable from `embed`.

**D5 (Low) — `AgentContextProtocol`** (`context.py:24-36`) leaks `.history`/`.compaction` internals.

---

## E. Runtime Safety

**E1 (High) — No cancellation or deadlines anywhere.**
`AgentRuntime`, `Tool.execute`, `LLMClient`, `MessageContext` have no cancellation token or timeout. HITL pauses and priority preemption (`AgentStep.PAUSED` exists with no mechanism) all need cooperative cancellation; retrofitting changes every signature.

**E2 (High) — Stream events cannot be ordered or attributed.**
`TextDelta`/`ReasoningDelta` carry no `seq`, no `agent_id`, no `run_id`. `AgentProgress` has no timestamp or sequence. Over Redis pub/sub, NATS, or SSE reconnect, interleaved multi-agent streams cannot be reassembled.
**Fix**: `seq`, source ids, timestamps on all stream events.

**E3 (Medium) — Inconsistent mutability.**
`content.py` models are frozen; `Memory`, `Document`, `SearchResult`, `Entity`, `Relationship`, `Skill` are mutable dataclasses with shared-mutable `metadata` dicts.

**E4 (Medium) — `ShortTermMemory` read-modify-write race** (`memory.py:83-100`)
`get_state`/`set_state` by concurrent agents in one session loses writes.

---

## F. Domain Modeling

**F1 (High) — Transport envelope conflated with conversation turn.**
`HistoryProvider` stores `Message` (routing envelope); `CompactionStrategy` operates on `list[Message]`; but `LLMClient` consumes `list[ChatMessage]`. Every agent converts at the boundary; persistence stores routing metadata it doesn't need.
**Fix**: History stores `ChatMessage` (the transcript); `Message` belongs to routing only.

**F2 (Medium) — `identity.py` is a grab-bag.**
`AgentId`/`TopicId` (identity) + `Supervision`/`Priority`/`HistoryRetention` (execution policy).

**F3 (Medium) — No tenancy dimension.**
`AgentId(type, key)`, `TopicId`, `Supervision` have no tenant/namespace. Cross-tenant topic collisions possible when the runtime distributes.

**F4 (Low) — Loose types.**
`ChatMessage.role: str` (no Role literal), no `name` for multi-agent attribution.

---

## G. Scalability (100 → 10,000 agents)

**G1 (High) — `AgentRuntime.send_message` is RPC-coupled.**
Returns the recipient's reply with in-process exception semantics. No way to express queueing, backpressure, flow control, or delivery guarantees at 1,000+ agents.

**G2 (Medium) — Inline `bytes` in media blocks flow through history and pub/sub.**
A 50 MB video in a hot loop is a memory and transport bomb.

**G3 (Low) — Non-sortable message IDs.**
`uuid4()` hex strings don't sort by insertion time; ULIDs aid distributed log ordering.

---

## H. Distributed Future

**H1 (Critical) — `Message` lacks serialization primitives.**
No `id` (dedup/idempotency), no `created_at`, no payload type tag, no schema version. Blocks every listed transport (Kafka, NATS, Restate, Temporal). Highest-leverage fix.

**H2 (High) — No serialization convention.**
Content/messages are pydantic; identity/stream/usage/vector/graph/memory are dataclasses with nested `AgentId` needing hand-rolled codecs. `redis_history` and `postgres_history` already serialize differently.

**H3 (High) — Two disjoint event systems.**
Kernel pub/sub (`TopicId` + `publish_message`) vs Redis `EventBus` in `serving/shared/events/` with its own dict event types. Event-driven orchestration will fracture across both.
**Fix**: Versioned kernel `Event` envelope that both systems carry.

**H4 (High) — No state snapshot/checkpoint contract.**
`fabric/durable/DurableRunner` is a skeleton. Temporal/Ray/LangGraph-style resume needs `save_state/load_state` on the Agent contract plus a `Checkpoint` value type.

**H5 (Medium) — `ApprovalHandler` referenced normatively** (`tools.py:42`)
but defined nowhere in the kernel. HITL (a stated future) has no L0 contract.

---

## I. Maintainability Ratings

| Module | Score | Key Issues |
|---|---|---|
| `content.py` | 7 | Closed union, silent fallback, vendor `detail` field |
| `identity.py` | 5 | Cohesion violation, policy constants, no tenancy |
| `message.py` | 3 | Untyped payload, mixes 3 concerns, duplicate tool types |
| `protocol.py` | 5 | RPC semantics, fat interface, no cancellation |
| `tools.py` | 4 | Concrete Toolbox + OpenAI specifics in L0 |
| `llm.py` | 3 | Protocol mismatches all implementations |
| `stream.py` | 4 | No ordering/attribution, AgentStep not an enum |
| `history.py` | 4 | Stores envelope not turn, can't honor RUN retention |
| `context.py` | 5 | Leaks internals, operates on Message not ChatMessage |
| `middleware.py` | 2 | Docstring claims false; three identical Any protocols |
| `errors.py` | 6 | No common base, inconsistent structured fields |
| `memory.py` | 6 | RMW race, ignores tenancy |
| `vector.py` | 6 | Parallel lists, mutable value types |
| `graph.py` | 5 | Cypher in contract |
| `skills.py` | 6 | Mutable, docstring references deleted ReActAgent |
| `usage.py` | 8 | Clean; could add reasoning_tokens |
| `__init__.py` | 6 | Export drift |

---

## J. Design Principle Summary

- **SRP**: Violated in `identity.py`, `message.py`
- **OCP**: Violated by closed ContentBlock union, concrete Toolbox, `query_cypher`
- **ISP**: `AgentRuntime` bundles transport + registry + lifecycle
- **DIP**: Structurally enforced; semantically violated by OpenAI shapes in L0
- **Hexagonal/Clean**: Ports exist and direction enforced (genuine strength); several ports mirror one adapter's shape
- **CQRS/EDA**: `correlation_id`/`causation_id` are right instincts; no command/event distinction, no event versioning; real event bus bypasses kernel

---

## K. Missing Kernel Capabilities

| Capability | Status |
|---|---|
| Cancellation token & deadlines | Missing |
| Agent lifecycle protocol with `save_state/load_state` | Missing |
| Checkpoint contract | Missing |
| Versioned Event envelope | Missing |
| HITL `ApprovalRequest/ApprovalHandler` | Missing |
| RunContext (deadline, trace, tenant) | Missing |
| Serialization convention for all kernel types | Missing |
| Common `KernelError` base | Missing |
| Structured-output contract for LLMs | Missing |
| Graph-execution primitives | Missing |
| Stream sequencing (seq number, source attribution) | Missing |
| Tool-result streaming / partial results | Missing |

---

## L. Architecture Scorecard

**Score: 6/10**

**Strengths**:
- Strict doubly-enforced dependency direction (import-linter + architecture tests)
- Small frozen surface with LOC/file ceilings
- Discriminated multimodal content union — best-in-class
- Session/run separation in supervision
- MCP-Apps-aware UI carrier (UIResourceBlock)
- Priority/budget model

**Weaknesses**:
- Untyped Message payload blocks all distributed transports
- LLMClient protocol divergent from all 6 implementations
- No Agent contract; agents are anonymous closures
- No cancellation; PAUSED step has no mechanism
- No serialization story; two incompatible history serializers already
- Dual event systems (kernel + serving/shared/events)
- Vendor formats embedded in L0 (OpenAI function shape, vision detail)

**Technical Debt**:
- Middleware TypeVar fiction
- Duplicate tool-call types
- Silent deserialization fallbacks
- Export drift (ToolType, EmbeddingResult missing from __init__)
- Docstrings referencing deleted ReActAgent, wrong layer names
- History API cannot honor its own HistoryRetention.RUN enum

---

## M. Competitive Analysis

| Framework | Ravi Advantage | Ravi Deficit |
|---|---|---|
| **AutoGen-core** | Stronger layering, richer multimodal content union | No `CancellationToken`, no `save_state/load_state`, no message serializer registry |
| **LangGraph** | Cleaner layer separation | No checkpointing, no interrupts (HITL), no graph execution at L0 |
| **PydanticAI** | No framework leakage into kernel | No typed `ModelSettings`, no structured-output contract, `**kwargs` everywhere |
| **OpenAI Agents SDK** | No vendor lock-in at L0 | No typed `RunContext`, handoffs are untyped string steps |
| **Semantic Kernel** | Protocol-first design | No typed filter pipeline (middleware uses Any) |
| **Haystack** | Better content model | No typed component sockets for future graph runtime |
| **CrewAI** | Stronger kernel discipline | — |

**Where ravi wins**: enforced layering, multimodal content model, supervision/priority tree, MCP Apps UI integration.
**Where ravi loses**: everything distributed, durable, and cancellable.

---

## Remediation

See plan file at `.claude/plans/you-are-a-principal-curious-squid.md` for the full four-phase remediation. Implementation tracked in git history from 2026-06-11 onward.
