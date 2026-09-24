# The kernel → agents → integrations stack — why, not just what

Root `CLAUDE.md` has the directory map and the import-linter contract table.
This doc is the *why* — the reasoning that should stop you from "just importing
it, it's easier" across a layer boundary.

## The rule

```
kernel (L0)        Pure contracts: Protocols, dataclasses, enums. No I/O.
    ↑ imported by
agents (L1)        Run a complete chatbot with zero infrastructure. Every kernel
                   Protocol gets exactly ONE default implementation here, using
                   the least infra that Protocol can possibly need.
    ↑ imported by
integrations (L2)  Everything that reaches outside the process: more storage
                   backends, more LLM vendors, tools, RAG, MCP, sandboxes.
```

`serving/` and `runtimes/` sit beside the stack, not in it (below). Enforced by
`uv run lint-imports` (4 contracts, `pyproject.toml` `[tool.importlinter]`).

## The design principle: contract, native default, adapters

This is the shape every capability follows, and the reason a consumer project
can adopt `agent-substrate` without adopting our infrastructure:

1. **kernel** defines the Protocol (`DocumentExtractor`, `HistoryProvider`,
   `LLMClient`, `ObjectStore`, ...).
2. **agents** ships one native implementation that needs almost nothing —
   local files or SQLite, one OpenAI-compatible client. `agents/` alone runs a
   real chatbot (`examples/01_foundations/00_standalone_zero_infra.py`).
3. **integrations** ships stronger implementations of the *same* Protocol
   (Postgres, S3, Redis, vendor SDKs, the PaddleOCR service).

A consumer swaps any of them by implementing the Protocol — the same move as
an MCP tool adapter, which maps someone else's server onto the kernel `Tool`
contract. Nothing in `agents/` or `kernel/` has to change or be forked.

| Protocol | L1 native default | L2 upgrades |
|---|---|---|
| `LLMClient` | `OpenAIChatCompletionClient` (any `/v1/chat/completions`, incl. local Ollama) | Responses-API, Anthropic, Gemini clients |
| `EmbeddingClient` | `SentenceTransformersEmbeddingClient` (local model) | OpenAI/Gemini embeddings, the reranker service |
| `HistoryProvider` | `LocalFilesystemHistoryProvider` | Redis, Postgres |
| `MemoryStore` / `ShortTermMemory` | `LocalFilesystemMemoryStore` / `...ShortTermMemory` | Postgres, Redis, Lance |
| `ObjectStore` | `WorkspaceFileStore` (local disk) | `S3FileStore` |
| `DocumentExtractor` | `LocalDocumentExtractor` (pdfplumber/pypdf + Tesseract fallback) | `ServiceBackedDocumentExtractor` (PaddleOCR) |
| `DocumentStore` | `LocalFilesystemDocumentStore` | — |
| runtime backends (`EventLog`, `Inbox`, `Scheduler`, ...) | `build_local_runtime()` (one SQLite file) | `build_postgres_runtime()` |

There is deliberately **no in-memory floor**: the minimum an `agents/` default
offers is durable local disk. (Tests may use in-memory doubles.)

## Why kernel is frozen

`kernel/` has zero I/O and no third-party dependency but pydantic. The payoff:
every backend can implement a kernel Protocol and be swapped without touching
`agents/` or `integrations/`. This is what makes the runtime stage migration
(see [`runtime-stages.md`](runtime-stages.md)) possible without a rewrite.

`tests/architecture/test_kernel_invariants.py` enforces, precisely: the layout
stays flat (only the permitted subpackages), no upward imports, no vendor
strings (`gpt-`, `claude-`, ...), pydantic as the only third-party dependency,
and the core wire types round-trip. There is **no** LOC or file-count ceiling —
an earlier version of this doc claimed one; nothing ever enforced it.

## Why `agents` (L1) can't import `integrations` (L2)

Agent *behavior* (the ReAct loop, guardrails, middleware, flows) must not depend
on *what tools or backends exist*. That is what lets you build a new agent type
without every tool wired up, and keeps the tool catalog swappable per
deployment. The reverse direction is fine: `integrations/` imports `agents/`
(and reuses its helpers — e.g. every vendor client calls
`agents.llm.modalities.fit_to_capabilities`).

## `serving/` and `runtimes/` are orthogonal

- `serving/` — the deployment shells (monolith, the 12 microservices, the SSE
  wire protocol) **and their composition root**, `serving/factory.py`, which
  builds the DI graph the monolith consumes. It may cross-import every layer,
  but its routes and services are still bound by
  `serving cannot import agents or integrations-that-were-capabilities`, with
  documented exceptions in `pyproject.toml`. `serving/factory.py` is the
  *primary* meeting point, not the only one.
- `runtimes/` — independently deployable, heavy-dependency, HTTP-only services
  (PaddleOCR, embedding/reranking) plus their thin clients. A service and its
  client live together so one unit is owned in one place; the client is what
  plugs into the stack (e.g. as a `DocumentExtractor`).

## Content and capabilities

Every `LLMClient` exposes `capabilities: ModelCapabilities` (input modalities,
context window, prices). A client must never send content outside
`capabilities.input_modalities`: it runs its messages through
`fit_to_capabilities` first, which swaps unsupported media for a short text
note — so a text-only model is told something was there rather than the
provider rejecting the request or the content silently vanishing.

A tool's images and documents reach the model *natively*: inside the tool
result for OpenAI's Responses API, Anthropic, and Gemini 3+; in a follow-up user
message where the API has no other way (Chat Completions, Gemini <3).
Compaction never drops them (`ToolResultCompactionStrategy` truncates only the
text).

## Who a message belongs to: `RunScope`

Tenant, user, conversation, branch and agent identity travel on `ctx.scope`
(`kernel.agent.runtime_context.RunScope`), set by the agent as it starts each
inbound message. Tools read it with `scope_of(ctx)` — never from ambient
globals, never from model-supplied arguments (a model steered by a document
can supply any id; that would be an authorization hole). A spawned sub-agent
inherits its parent's scope via `RunScope.child_metadata()`.

## Common mistake this catches

Reaching into `agents/core/react.py` from a new tool to "just check something
about the agent". A tool *receives* the `ctx: RunContext` already passed to its
`execute()`; that is the sanctioned crossing point — tools don't construct or
inspect agent internals.
