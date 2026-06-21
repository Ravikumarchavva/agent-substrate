# ravi-engine v1 Cleanup Plan

**Scope:** `src/ravi/` only. Tests in `tests/` are addressed in section 7.  
**Goal:** Every file consistent, no dead code, no stale docs, import-linter passes, `uv run pytest` green.  
**Rule:** No new abstractions. No patching over problems. Fix what exists; delete what is dead.  
**Verification command (run after every section):**
```bash
uv run python -m ruff check src/ravi && uv run lint-imports && uv run pytest -q
```

---

## Actual package structure (ground truth)

```
src/ravi/
├── kernel/          L0 — frozen contracts (content, message, tools, llm, history, stream, …)
├── agents/          L1-L3 merged — context, llm, guardrails, middleware, orchestrator, runtime, assistant, flow, proxy
├── adapters/        orthogonal I/O ports — llm (openai/anthropic/gemini), memory, mcp, vector, graph, storage, events, spotify
├── capabilities/    orthogonal — tools, skills, knowledge, connectors, internal (scanner/pipeline/skills), triggers
├── serving/         orthogonal — monolith (fastapi app), services (12 microservices), shared (auth, db, events, …)
├── evals/           LLM-as-judge eval framework
├── config.py        Pydantic Settings
├── cli.py           CLI entry point
├── console.py       Interactive console
├── logger.py        Logging setup
└── exceptions.py    Public exceptions
```

Import-linter enforces two contracts (already passing):
1. `agent_substrate.agents` → `agent_substrate.kernel` (stack flows downward)
2. `agent_substrate.kernel` imports nothing from any other ravi package

---

## Section 1 — Delete dead code (do this first)

### 1.1 Delete two parked event adapter files

These files import `agent_substrate.kernel.events._fabric` which does not exist and was never created.
They are explicitly marked as parked in `adapters/events/__init__.py` and are unreachable.

**Delete:**
- `src/ravi/adapters/events/_redis_fanout.py`
- `src/ravi/adapters/events/_redis_log.py`

`adapters/events/__init__.py` already only exports `EventBus` from `redis_event_bus.py` — no change needed there.

### 1.2 Delete the duplicate guest_agent directory

`capabilities/tools/code_interpreter/guest_agent/guest_agent/` is a nested duplicate of `capabilities/tools/code_interpreter/guest_agent/`. Both contain `agent.py`. This double-nesting is wrong.

Inspect both `agent.py` files and determine if they are the same.  
- If identical: delete `guest_agent/guest_agent/` entirely.  
- If different: keep the outer one, delete the inner one, merge any unique logic.

**No `__init__.py` is needed** in `guest_agent/` — it is a standalone script, not a library package.

---

## Section 2 — Add missing `serving/__init__.py`

`src/ravi/serving/` is a directory with no `__init__.py`. This means `import agent_substrate.serving` fails with `ModuleNotFoundError`, even though three sub-packages (`monolith`, `services`, `shared`) exist inside it.

**Create** `src/ravi/serving/__init__.py` with this content:

```python
"""agent_substrate.serving — deployment shells for the agent framework.

Three sub-packages:
  monolith/   — single FastAPI application
  services/   — 12 independent microservices
  shared/     — cross-service auth, database, events, observability
"""

from __future__ import annotations
```

No re-exports needed — consumers import directly from sub-packages.

---

## Section 3 — Add `from __future__ import annotations` to every Python file

Every `.py` file under `src/ravi/` must start with `from __future__ import annotations`
(after the module docstring if present, before any other imports).

The following files are missing it. Add it to each one — do not change anything else in the file.

### 3.1 adapters/

- `adapters/__init__.py`
- `adapters/events/__init__.py`
- `adapters/graph/__init__.py`
- `adapters/llm/__init__.py`
- `adapters/llm/anthropic/__init__.py`
- `adapters/llm/gemini/__init__.py`
- `adapters/llm/openai/__init__.py`
- `adapters/mcp/__init__.py`
- `adapters/mcp/apps/__init__.py`
- `adapters/mcp/tool.py`
- `adapters/memory/__init__.py`
- `adapters/spotify/__init__.py`
- `adapters/storage/__init__.py`
- `adapters/vector/__init__.py`

### 3.2 agents/

- `agents/assistant/__init__.py`
- `agents/context/__init__.py`
- `agents/flow/__init__.py`
- `agents/hooks/__init__.py`
- `agents/llm/__init__.py`
- `agents/middleware/__init__.py`
- `agents/orchestrator/__init__.py`
- `agents/proxy/__init__.py`
- `agents/supervision/__init__.py`

### 3.3 capabilities/

- `capabilities/connectors/__init__.py`
- `capabilities/connectors/email/__init__.py`
- `capabilities/connectors/google_calendar/__init__.py`
- `capabilities/connectors/minio_storage/__init__.py`
- `capabilities/connectors/postgres_query/__init__.py`
- `capabilities/knowledge/__init__.py`
- `capabilities/knowledge/loaders/__init__.py`
- `capabilities/skills/__init__.py`
- `capabilities/skills/api_testing/__init__.py`
- `capabilities/skills/code_explainer/__init__.py`
- `capabilities/skills/code_review/__init__.py`
- `capabilities/skills/data_analysis/__init__.py`
- `capabilities/skills/debugging/__init__.py`
- `capabilities/skills/project_planning/__init__.py`
- `capabilities/skills/spotify_player/__init__.py`
- `capabilities/skills/summarization/__init__.py`
- `capabilities/skills/web_research/__init__.py`
- `capabilities/skills/writing_assistant/__init__.py`
- `capabilities/tools/__init__.py`
- `capabilities/tools/chain_executor/__init__.py`
- `capabilities/tools/code_interpreter/__init__.py`
- `capabilities/tools/code_interpreter/code_interpreter/__init__.py`
- `capabilities/tools/document_analyzer/__init__.py`
- `capabilities/tools/email_sender/__init__.py`
- `capabilities/tools/http_request/__init__.py`
- `capabilities/tools/human_input/__init__.py`
- `capabilities/tools/image_generator/__init__.py`
- `capabilities/tools/invoice_extractor/__init__.py`
- `capabilities/tools/knowledge_search/__init__.py`
- `capabilities/tools/memory/__init__.py`
- `capabilities/tools/pipeline_manager/__init__.py`
- `capabilities/tools/task_manager/__init__.py`
- `capabilities/tools/tool_search/__init__.py`
- `capabilities/tools/web_surfer/__init__.py`

### 3.4 serving/

- `serving/monolith/__init__.py`
- `serving/monolith/database.py`
- `serving/monolith/routes/__init__.py`
- `serving/monolith/security/__init__.py`
- `serving/monolith/services/__init__.py`
- `serving/monolith/sse/__init__.py`
- `serving/services/__init__.py`
- `serving/services/admin/__init__.py`
- `serving/services/agent_runtime/__init__.py`
- `serving/services/code_interpreter/__init__.py`
- `serving/services/conversation/__init__.py`
- `serving/services/gateway/__init__.py`
- `serving/services/human_gate/__init__.py`
- `serving/services/identity/__init__.py`
- `serving/services/job_controller/__init__.py`
- `serving/services/live_stream/__init__.py`
- `serving/services/policy/__init__.py`
- `serving/services/tool_executor/__init__.py`
- `serving/shared/__init__.py`
- `serving/shared/auth/__init__.py`
- `serving/shared/contracts/__init__.py`
- `serving/shared/database/__init__.py`
- `serving/shared/observability/__init__.py`
- `serving/shared/observability/telemetry.py`
- `serving/shared/tasks/__init__.py`

### 3.5 evals/ and top-level

- `evals/__init__.py`
- `console.py`

---

## Section 4 — Fix blank `__init__.py` files

Many `__init__.py` files are completely empty or contain only a blank docstring. They need either:
- Proper re-exports (if the package is intended to be imported from directly), or
- A one-line docstring only (if the package is convention-scanned and not directly importable).

### 4.1 Tool packages — add docstring only (convention-scanned, not re-exported)

The scanner in `capabilities/internal/scanner.py` discovers tools by walking the directory.
Tools are **not** re-exported from their `__init__.py`. Each tool `__init__.py` should contain:

```python
"""<ToolName> — one-line description of what this tool does."""

from __future__ import annotations
```

Apply this pattern to all of these (write the correct description for each):
- `capabilities/tools/chain_executor/__init__.py`
- `capabilities/tools/code_interpreter/__init__.py`
- `capabilities/tools/document_analyzer/__init__.py`
- `capabilities/tools/email_sender/__init__.py`
- `capabilities/tools/http_request/__init__.py`
- `capabilities/tools/human_input/__init__.py`
- `capabilities/tools/image_generator/__init__.py`
- `capabilities/tools/invoice_extractor/__init__.py`
- `capabilities/tools/knowledge_search/__init__.py`
- `capabilities/tools/memory/__init__.py`
- `capabilities/tools/pipeline_manager/__init__.py`
- `capabilities/tools/task_manager/__init__.py`
- `capabilities/tools/tool_search/__init__.py`
- `capabilities/tools/web_surfer/__init__.py`

### 4.2 Skill packages — add docstring only

Skills are discovered by the SkillLoader from `SKILL.md` frontmatter. Each skill `__init__.py`:

```python
"""<skill_name> skill — one-line description."""

from __future__ import annotations
```

Apply to:
- `capabilities/skills/api_testing/__init__.py`
- `capabilities/skills/code_explainer/__init__.py`
- `capabilities/skills/code_review/__init__.py`
- `capabilities/skills/data_analysis/__init__.py`
- `capabilities/skills/debugging/__init__.py`
- `capabilities/skills/project_planning/__init__.py`
- `capabilities/skills/spotify_player/__init__.py`
- `capabilities/skills/summarization/__init__.py`
- `capabilities/skills/web_research/__init__.py`
- `capabilities/skills/writing_assistant/__init__.py`

### 4.3 Adapter sub-provider packages — add docstring only

Provider sub-packages (`anthropic/`, `gemini/`, `openai/`) contain implementation files
but are imported directly, not re-exported from the sub-package `__init__.py`.

```python
"""agent_substrateadapters.llm.<provider> — <provider> LLM adapter."""

from __future__ import annotations
```

Apply to:
- `adapters/llm/anthropic/__init__.py`
- `adapters/llm/gemini/__init__.py`
- `adapters/llm/openai/__init__.py`
- `adapters/mcp/apps/__init__.py`

### 4.4 Connector packages — add docstring only

```python
"""agent_substrate.capabilities.connectors.<name> — <connector> connector."""

from __future__ import annotations
```

Apply to:
- `capabilities/connectors/email/__init__.py`
- `capabilities/connectors/google_calendar/__init__.py`
- `capabilities/connectors/minio_storage/__init__.py`
- `capabilities/connectors/postgres_query/__init__.py`

### 4.5 Agent sub-packages — add docstring only

These are imported through `agents/__init__.py` (which already re-exports everything).
The sub-package `__init__.py` files should have just a docstring:

```python
"""agent_substrate.agents.<subpackage> — one-line description."""

from __future__ import annotations
```

Apply to:
- `agents/assistant/__init__.py`
- `agents/context/__init__.py`
- `agents/flow/__init__.py`
- `agents/hooks/__init__.py`
- `agents/llm/__init__.py` — NOTE: `agents/llm/__init__.py` already has real re-exports and `__all__`. Do NOT replace it. Only add `from __future__ import annotations` at the top.
- `agents/middleware/__init__.py` — same, already has exports, only add future annotation
- `agents/orchestrator/__init__.py`
- `agents/proxy/__init__.py`
- `agents/supervision/__init__.py`

### 4.6 Serving sub-packages — docstring only

These are never imported as packages directly — always their sub-modules.

```python
"""agent_substrate.serving.<subpackage> — one-line description."""

from __future__ import annotations
```

Apply to all the blank `__init__.py` in:
- `serving/monolith/__init__.py`
- `serving/monolith/routes/__init__.py`
- `serving/monolith/security/__init__.py`
- `serving/monolith/services/__init__.py`
- `serving/monolith/sse/__init__.py`
- `serving/services/__init__.py`
- `serving/services/admin/__init__.py`
- `serving/services/agent_runtime/__init__.py`
- `serving/services/code_interpreter/__init__.py`
- `serving/services/conversation/__init__.py`
- `serving/services/gateway/__init__.py`
- `serving/services/human_gate/__init__.py`
- `serving/services/identity/__init__.py`
- `serving/services/job_controller/__init__.py`
- `serving/services/live_stream/__init__.py`
- `serving/services/policy/__init__.py`
- `serving/services/tool_executor/__init__.py`
- `serving/shared/__init__.py`
- `serving/shared/auth/__init__.py`
- `serving/shared/contracts/__init__.py`
- `serving/shared/database/__init__.py`
- `serving/shared/observability/__init__.py`
- `serving/shared/tasks/__init__.py`

---

## Section 5 — Fix the CLAUDE.md (documentation sync)

`ravi-engine/CLAUDE.md` describes an architecture that no longer exists. It references:
- `fabric/`, `reasoning/`, `orchestration/` — these are now all merged into `agents/`
- `extensions/` — does not exist (was renamed to `agents/` + `capabilities/`)
- `integrations/` — does not exist (was renamed to `adapters/`)
- `kernel/plugin/registry.py` — does not exist
- `BaseAgent`, `BaseModelClient`, `ReActAgent` — all deleted
- `catalog/` — does not exist (was renamed to `capabilities/`)
- `shared/` — now at `serving/shared/`

**Rewrite the following sections of `ravi-engine/CLAUDE.md` to match reality:**

### 5.1 Directory map

Replace the existing `src/ravi/` tree with this accurate version:

```
src/ravi/
├── kernel/       L0 — FROZEN. Pure contracts: types, Protocols, dataclasses. No I/O.
│   ├── content.py        ContentBlock, TextBlock, ChatMessage, ToolUseBlock, …
│   ├── message.py        Message, MessageContext, MessageHandler, Subscription
│   ├── tools.py          Tool Protocol, ToolExecutionResult, ToolRisk, ToolCallRequest
│   ├── llm.py            LLMClient, EmbeddingClient Protocols
│   ├── history.py        HistoryProvider Protocol
│   ├── context.py        CompactionStrategy, AgentContextProtocol
│   ├── middleware.py     Interceptor Protocol
│   ├── stream.py         TextDelta, ReasoningDelta, CompletionEvent, StreamDone
│   ├── protocol.py       AgentRuntime Protocol
│   ├── identity.py       AgentId, TopicId
│   └── errors.py
│
├── agents/       L1-L3 combined — context, LLM clients, guardrails, middleware, agent types
│   ├── assistant/        AssistantAgent (ReAct loop, tools, HITL, guardrail injection)
│   ├── context/          AgentContext, InMemoryHistoryProvider, compaction strategies
│   ├── llm/              LLMClient (concrete), EmbeddingClient, model registry, cache/fallback/router
│   ├── guardrails/       GuardrailType, run_guardrails, ContentFilter, LLMJudge, MaxToken, PII, PromptInjection
│   ├── middleware/       Interceptor, MiddlewarePipeline, AuditLogger
│   ├── flow/             FlowAgent (multi-step graph execution)
│   ├── orchestrator/     OrchestratorAgent
│   ├── proxy/            UserProxyAgent
│   ├── runtime/          LocalRuntime (message dispatch)
│   ├── hooks/            lifecycle hooks
│   ├── resources/        ExecutionBudget, agent_span
│   └── supervision/      RetryPolicy
│
├── adapters/     concrete I/O ports implementing kernel/agents contracts
│   ├── llm/              openai/, anthropic/, gemini/, encoders/, factory.py
│   ├── memory/           redis_history.py, postgres_history.py
│   ├── mcp/              MCPClient, MCPTool, apps/
│   ├── vector/           vector store adapters
│   ├── graph/            graph store adapters
│   ├── storage/          file storage adapters
│   ├── events/           EventBus (Redis pub/sub)
│   └── spotify/          Spotify API adapter
│
├── capabilities/ the agent's runtime capabilities
│   ├── tools/            tool implementations (human_input, task_manager, web_surfer, …)
│   ├── skills/           SKILL.md prompt-skill packages
│   ├── knowledge/        RAG pipeline, loaders, graph_rag
│   ├── connectors/       external service connectors (email, calendar, minio, postgres)
│   ├── triggers/         event-based trigger definitions
│   └── internal/         scanner, pipeline engine, skill loader, chain runtime
│
├── serving/      deployment shells
│   ├── monolith/         single FastAPI app (app.py, routes/, sse/, security/, services/)
│   ├── services/         12 independent microservices (one FastAPI app per folder)
│   └── shared/           cross-service: auth, database, events, observability, tasks
│
├── evals/        LLM-as-judge eval framework (judge, criteria, runner, models)
├── config.py     Pydantic Settings (reads .env)
├── exceptions.py public exceptions (GuardrailTripwireError, …)
├── logger.py     setup_logging()
├── cli.py        CLI entry point
└── console.py    interactive console
```

### 5.2 Key patterns section

Replace all references to `BaseTool`, `BaseModelClient`, `ReActAgent`, `extensions/`, `integrations/`, `catalog/`, `shared/` with the correct current equivalents:

**Tool creation** (replace the BaseTool example):
```python
from agent_substrate.kernel.tools import ToolExecutionResult, Tool
from agent_substrate.kernel.content import TextBlock

class MyTool:
    name = "my_tool"
    description = "What it does"
    input_schema = {"type": "object", "properties": {...}, "required": [...]}

    async def execute(self, *, param: str) -> ToolExecutionResult:  # type: ignore[override]
        return ToolExecutionResult(content=[TextBlock(text="result")])
```

Tools are auto-discovered by `capabilities/internal/scanner.py` when placed in `capabilities/tools/<name>/tool.py`. No registration decorator needed.

**LLM client** (replace wrong import path):
```python
from agent_substrateadapters.llm.factory import create_model_client
client = create_model_client("gpt-4o", api_keys={"openai": "..."})
```

**Memory / history** (replace RedisMemory references):
```python
# In-memory (default, for testing)
from agent_substrate.agents.context import InMemoryHistoryProvider

# Redis-backed
from agent_substrateadapters.memory.redis_history import RedisHistoryProvider

# Postgres-backed
from agent_substrateadapters.memory.postgres_history import PostgresHistoryProvider
```

**Adding a new capability** (replace the old @register_* table):

| You want to add… | Write it in… |
|---|---|
| A new agent type | `agents/<name>/agent.py` — subclass `AssistantAgent` or follow its pattern |
| A new guardrail | `agents/guardrails/<name>.py` — implement `run(ctx) -> GuardrailResult` |
| A new LLM provider | `adapters/llm/<provider>/` — implement `LLMClient` Protocol from `kernel/llm.py` |
| A new memory backend | `adapters/memory/<name>.py` — implement `HistoryProvider` Protocol from `kernel/history.py` |
| A new tool | `capabilities/tools/<name>/tool.py` — implement `Tool` Protocol (auto-scanned) |
| A new skill | `capabilities/skills/<name>/SKILL.md` — YAML frontmatter + prompt body |

### 5.3 Environment variables

Remove `REDIS_SESSION_TTL`, `SESSION_MAX_MESSAGES`, `SESSION_AUTO_CHECKPOINT`, `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `CODE_INTERPRETER_URL`, `SYSTEM_INSTRUCTIONS` from the env section if they are not read in `config.py`. Verify each one against `src/ravi/config.py` before removing.

### 5.4 Known tech debt section

Remove references to `server/routes/spotify_oauth.py`, `shared/tasks/store.py`, `core/agents/react_agent.py` — these paths do not exist. Replace with accurate current paths after grepping.

---

## Section 6 — Verify and complete `ravi/__init__.py` public API

The top-level `src/ravi/__init__.py` is the public face of the package. It uses lazy loading.

Verify each entry in `_LAZY` dict actually resolves:
```python
import importlib
_LAZY = {
    "AssistantAgent":   ("agent_substrate.agents.assistant.agent", "AssistantAgent"),
    "AgentRunResult":   ("agent_substrate.agents.assistant.agent", "AgentRunResult"),
    "LocalRuntime":     ("agent_substrate.agents.runtime",         "LocalRuntime"),
    "Skill":            ("agent_substrate.agents.skills",          "Skill"),  # ← verify this module exists
    ...
}
for name, (mod, attr) in _LAZY.items():
    obj = getattr(importlib.import_module(mod), attr)
    assert obj is not None, f"Missing: {mod}.{attr}"
```

If `agent_substrate.agents.skills` does not exist, fix the import path (likely `agent_substrate.capabilities.skills` or `agent_substrate.capabilities.internal.skill_models`).

Also add the following to `__all__` and `_LAZY` — these are commonly needed but missing from the top-level API:

```python
# history providers
"InMemoryHistoryProvider": ("agent_substrate.agents.context", "InMemoryHistoryProvider"),
"AgentContext":            ("agent_substrate.agents.context", "AgentContext"),
"SlidingWindowCompaction": ("agent_substrate.agents.context", "SlidingWindowCompaction"),
# kernel types (already importable from agent_substrate.kernel but convenient at top level)
"ChatMessage":             ("agent_substrate.kernel.content",  "ChatMessage"),
"TextBlock":               ("agent_substrate.kernel.content",  "TextBlock"),
"ToolExecutionResult":     ("agent_substrate.kernel.tools",    "ToolExecutionResult"),
```

---

## Section 7 — Tests: rewrite legacy tests for current API

The active test suite is under `tests/` (not `tests/legacy/`). Currently:
- `tests/architecture/test_kernel_invariants.py` — kernel LOC/file-count ceilings (passes)
- `tests/reasoning/test_assistant_agent.py` — AssistantAgent with mock LLM (passes)
- `tests/server/` — monolith health, event bus, gateway chat, settings, OAuth (passes)

`tests/legacy/` contains ~100 test files written against deleted APIs. They are excluded via `norecursedirs` in `pyproject.toml`.

**Write new tests for the following — one file per module, placed in `tests/`:**

### Priority 1 — kernel contracts (pure unit tests, no I/O)

- `tests/kernel/test_content.py` — `ChatMessage`, `TextBlock`, `ToolUseBlock`, `content_blocks_to_str`, `content_block_from_dict`
- `tests/kernel/test_tools.py` — `ToolExecutionResult`, `ToolRisk`, `ToolCallRequest`, `Tool` protocol satisfaction
- `tests/kernel/test_stream.py` — `TextDelta`, `CompletionEvent`, `StreamDone` construction and fields
- `tests/kernel/test_history.py` — `HistoryProvider` protocol (use `InMemoryHistoryProvider` as the impl under test)

### Priority 2 — agents layer (mock LLM already in fixtures)

- `tests/agents/test_guardrails.py` — `MaxTokenGuardrail`, `ContentFilterGuardrail`, `run_guardrails()`
- `tests/agents/test_context.py` — `AgentContext`, `SlidingWindowCompaction`, `InMemoryHistoryProvider.append/get_messages/clear`
- `tests/agents/test_middleware.py` — `MiddlewarePipeline`, `AuditLoggerMiddleware`
- `tests/agents/test_llm_router.py` — `ModelRouter.route()` with mock tiers

### Priority 3 — capabilities (pure unit tests where possible)

- `tests/capabilities/test_scanner.py` — `CatalogScanner.scan()` discovers tools in a temp directory
- `tests/capabilities/test_skill_loader.py` — `SkillLoader` loads SKILL.md, parses frontmatter

Use existing `tests/fixtures/mock_llm.py` and `tests/fixtures/fake_tools.py` — do not recreate fixtures.

### Priority 4 — adapters (integration tests, require real services or mocks)

Mark integration tests with `@pytest.mark.integration` and keep them skippable without Redis/Postgres:
- `tests/adapters/test_redis_history.py` — `RedisHistoryProvider.append/get_messages` (skip if no Redis)
- `tests/adapters/test_postgres_history.py` — `PostgresHistoryProvider` (skip if no DB)
- `tests/adapters/test_mcp_tool.py` — `MCPTool` wrapping and schema methods

---

## Section 8 — Final verification

Run the full preflight in this exact order:

```bash
cd ravi-engine

# 1. Dependencies
uv sync

# 2. Lint — must be zero errors
uv run python -m ruff check src/ravi

# 3. Format check
uv run python -m ruff format src/ravi --check

# 4. Import layer contracts — must show "2 kept, 0 broken"
uv run lint-imports

# 5. Critical import smoke test
uv run python -c "
import ravi
import agent_substrate.kernel
import agent_substrate.agents
import agent_substrateadapters
import agent_substrateadapters.llm
import agent_substrateadapters.memory
import agent_substrateadapters.mcp
import agent_substrateadapters.events
import agent_substrate.capabilities.internal.scanner
import agent_substrate.serving.monolith.app
import agent_substrate.serving.services.tool_executor.executor
print('All imports OK')
"

# 6. Top-level lazy API smoke test
uv run python -c "
from ravi import (
    AssistantAgent, AgentRunResult, LocalRuntime,
    ContentFilterGuardrail, GuardrailType, LLMJudgeGuardrail,
    MaxTokenGuardrail, create_model_client,
    TextDelta, CompletionEvent, StreamDone,
    GuardrailTripwireError,
)
print('Public API OK')
"

# 7. Full test suite
uv run pytest -q

# Expected: all tests pass, 0 errors, 0 warnings about imports
```

---

## What NOT to do

- **Do not touch `src/ravi/kernel/`** — it is frozen. No new files, no new exports.
- **Do not add `__init__.py` to `serving/monolith/prompts/`** — it contains prompt text files, not Python modules.
- **Do not add `__init__.py` to `capabilities/tools/code_interpreter/guest_agent/`** — it is a standalone agent script, not a library.
- **Do not refactor or reorganize** — only add missing annotations, docstrings, delete dead files, fix CLAUDE.md, write tests.
- **Do not add backward-compat shims** — if something is deleted, it is deleted.
- **Do not convert any `# type: ignore[override]`** in tool `execute()` methods — this is intentional (keyword-only params violate the base Protocol signature by design).
- **Always use `uv run`** — never bare `python`, `pytest`, `ruff`.
