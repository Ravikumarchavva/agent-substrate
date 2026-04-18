# Agent Framework — GitHub Copilot Instructions

> **Full reference**: See [`CLAUDE.md`](../CLAUDE.md) for the complete directory map,
> environment variables, Docker ports, observability stack, eval framework, and coding standards.
> This file covers only the essential patterns and critical rules.

---

## Project Overview
Python async AI-agent framework built on **FastAPI** + **PostgreSQL** + **Redis**.
Two deployment modes: **monolith** (`server/`) and **microservices** (`services/` — 12 services).
Stack: Python 3.13, `uv` (never pip), SQLAlchemy 2 async, asyncpg, OpenTelemetry.

---

## Repository Structure (top-level)
```
raavan/
├── src/raavan/          ← Python package
│   ├── core/            ← Framework primitives (agents, memory abstractions, tools base, context, messages, guardrails)
│   │                        Includes: core/llm/ (BaseModelClient ABC — text + audio + vision)
│   │                        Includes: core/agents/ (_tool_execution, _guardrail_runner, _stream_handler)
│   ├── integrations/    ← External adapters (LLM, audio, MCP, Spotify) + concrete backends
│   │                        Includes: integrations/memory/ (RedisMemory, PostgresMemory)
│   │                                  integrations/storage/ (S3FileStore)
│   ├── catalog/         ← Unified capability system (tools/, skills/, connectors/, _skill_*.py)
│   ├── shared/          ← Cross-service contracts, auth, events, database, observability (incl. logger.py)
│   ├── server/          ← Monolith FastAPI server (app.py, _lifespan.py, routes/, security/, services/, sse/)
│   ├── services/        ← 12 microservices (gateway, identity, agent_runtime, conversation, …)
│   ├── configs/         ← Pydantic Settings
│   └── evals/           ← LLM-as-judge evaluation framework
├── deployment/
│   ├── docker/          ← Dockerfiles, docker-compose.yml, docker-compose.microservices.yml
│   └── k8s/             ← Kustomize base + Kind overlay, smoke-test.ps1
├── deploy.py            ← Cross-platform Kind cluster deploy script
├── docs/                ← Architecture, operations, design patterns
└── examples/            ← Jupyter notebooks (01–14)
```

---

## CI — CRITICAL RULE

Every repo has a `Makefile` with a `make ci` target that runs the full local preflight:
**sync → lint → typecheck-soft → test-ci → security-soft**

**After making any code changes, always run `make ci` to verify nothing is broken.**
This is mandatory before committing or considering work complete.

```bash
make ci
```

---

## Key Patterns

> Full reference: [`docs/design_patterns.md`](../docs/design_patterns.md)

Organized by GoF category:

### Creational (Object Creation)

| Pattern | Location | Rule |
|---|---|---|
| **Factory Method** | `core/storage/factory.py` | Use `create_file_store(settings)` — never import concrete store. |
| **Registry** | `core/tools/catalog.py` | Register via `catalog.register_tool(...)`. Search is global. |
| **Convention Discovery** | `catalog/_scanner.py` | Walks `catalog/tools/`; anchors on **last** `raavan` in path. |

### Structural (Object Composition)

| Pattern | Location | Rule |
|---|---|---|
| **Abstract Base Class** | `core/storage/base.py`, `core/agents/base_agent.py` | Subclasses implement abstract methods. |
| **Adapter** | `integrations/mcp/tool.py` | Three schema methods: `get_schema()`, `get_openai_schema()`, `get_mcp_schema()`. |
| **Proxy** | `catalog/_chain_runtime.py` | `ChainRuntime` assembles tool namespace; `AdapterProxy` wraps tools. |
| **Decorator** | `core/storage/encrypted.py` | `EncryptedFileStore` wraps `FileStore` for transparent encryption. |

### Behavioral (Object Interaction)

| Pattern | Location | Rule |
|---|---|---|
| **Template Method** | `core/tools/base_tool.py` | Subclass `BaseTool`, implement `execute()` with `# type: ignore[override]` for keyword-only params. |
| **Strategy** | `core/tools/base_tool.py` | Set `risk = ToolRisk.CRITICAL` and `hitl_mode = HitlMode.BLOCKING` **as class-level attributes**. |
| **Observer/Event Bus** | `server/sse/events.py`, `shared/events/types.py` | Always use factory functions: `workflow_started(...)` — **never build dicts manually**. |
| **Protocol duck typing** | `core/agents/base_agent.py` | `PromptEnricher` is `@runtime_checkable` — keeps `core/` free of `integrations/`. |
| **Pipeline Builder** | `core/pipelines/runner.py` | JSON graph → live objects via topology detection. |
| **ReAct Agent Loop** | `core/agents/react_agent.py` | Think → Act → Observe; guardrails at INPUT, OUTPUT, TOOL_CALL. |

### Architectural (System-wide)

| Pattern | Location | Rule |
|---|---|---|
| **DI via `app.state`** | `server/app.py` | Mount all objects in `lifespan`. Read via `request.app.state.*`. |

### Tool creation — always subclass `BaseTool`
```python
from raavan.core.tools.base_tool import BaseTool, ToolResult, ToolRisk, HitlMode

class EmailSenderTool(BaseTool):
    risk = ToolRisk.CRITICAL           # ← Strategy: class-level
    hitl_mode = HitlMode.BLOCKING      # ← Strategy: class-level

    def __init__(self):
        super().__init__(
            name="send_email",
            description="Send critical email",
            input_schema={"type": "object", "properties": {...}}
        )

    async def execute(self, *, to: str, subject: str, body: str) -> ToolResult:  # type: ignore[override]
        # BaseTool.run() validates inputs before calling this
        result = await send_email_service(to, subject, body)
        return ToolResult(
            content=[{"type": "text", "text": f"Email sent to {to}"}],
            app_data={"message_id": result.id}  # ← use app_data not metadata
        )
```

### SSE event bus (monolith)
```python
bridge: WebHITLBridge = request.app.state.bridge
await bridge.put_event({"type": "my_event", "data": {...}})
```

### New route
1. Create `server/routes/my_feature.py` with `router = APIRouter(prefix="/my-feature")`
2. Mount in `server/app.py → create_app()` via `app.include_router(...)`

---

## Message Content Formats

| Message type | `content` type |
|---|---|
| `SystemMessage` | `str` |
| `UserMessage` | `list[ContentPart]` |
| `AssistantMessage` | `Optional[list[MediaType]]` — list or `None` (tool-call-only) |
| `ToolExecutionResultMessage` | `str` (+ `tool_call_id`, `name`) |

`ToolCallMessage` fields: `tc.name`, `tc.arguments` (dict) — **not** `tc.function['name']`.

---

## Memory — CRITICAL RULES

- All `Memory` methods are `async def`. **Always `await`** them.
- **Never** add sync/async pairs (`add_message_sync`, etc.).
- One Redis class: `RedisMemory`. No wrappers.
- Lifecycle: `connect()` → use → `disconnect()`. No `close()` method.

---

## `.env` Rule
Never add inline comments after integer values.
✅ `REDIS_SESSION_TTL=3600`   ❌ `REDIS_SESSION_TTL=3600  # seconds`

---

## Conventions
- **Async everywhere** — every handler, tool, DB call is `async def`.
- **`from __future__ import annotations`** at top of every file.
- **No bare `except:`** — always catch specific exceptions.
- **`app.state.*`** is the DI container.
- **`uv` only** — never pip.

---

## Non-obvious Rules
| **LLM abstract base** | `core/llm/base_client.py` | `BaseModelClient` ABC lives in `core/` — no external deps. Audio methods (`transcribe`, `stream_tts`, `create_s2s_session`, `s2s_ws_url`) are optional (raise `NotImplementedError`). |
| **LLM concrete impl** | `integrations/llm/openai/openai_client.py` | `OpenAIClient` handles text + vision + image generation + STT + TTS + Realtime S2S. Import: `from raavan.integrations.llm.openai.openai_client import OpenAIClient` |
| **Audio in routes** | `server/routes/audio.py` | Use `request.app.state.model_client` (not `audio_client`). Check `model_client.supports_s2s` before S2S calls. |
| **Image input** | `core/messages/_types.py` | Use `ImageContent(url=...)`, `ImageContent(file_id=...)`, or `ImageContent(data=bytes)` in `UserMessage.content`. PIL `Image.Image` also accepted. |
| **Image generation** | `integrations/llm/openai/openai_client.py` | `await client.generate_image(prompt, model="dall-e-3")` — check `client.supports_image_generation` first. Returns `list[str]` (URLs or data URLs). |
| **Memory backends** | `integrations/memory/` | `RedisMemory`, `PostgresMemory` — concrete SDK-backed stores. |
| **S3 storage** | `integrations/storage/s3.py` | `S3FileStore` — concrete aiobotocore adapter. |
| **Skills infra** | `catalog/_skill_manager.py`, `_skill_loader.py`, `_skill_models.py` | Import via `from raavan.catalog import SkillManager` |
| **Logging** | `shared/observability/logger.py` | `setup_logging()` — structured JSON + pretty formatter. |
- Event factory functions only — never build event dicts manually.
- `server/security/` delegates to `shared/auth/` (thin wrapper binding settings).
- DB session: use `get_db_session` from `shared.database.dependency` — never local `_get_db`.
- Canonical enum re-exports: `from raavan.core import ToolRisk, RunStatus`.
