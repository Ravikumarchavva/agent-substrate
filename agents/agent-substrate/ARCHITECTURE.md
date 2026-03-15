# Agent Framework — Architecture Guide

This document explains the codebase structure, the rules that govern how layers
interact, and step-by-step guides for every common extension point.

---

## Layer Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│  server/  (FastAPI app, routes, DB models, services)                     │
│  • app.py — factory + lifespan (DI wiring)                               │
│  • routes/ — one file per feature (chat, tasks, hitl, threads, …)        │
│  • services/ — business logic extracted from routes                      │
│  • database.py — SQLAlchemy async session factory                        │
│  • schemas.py — Pydantic request / response models                       │
└────────────────────┬─────────────────────────────────────────────────────┘
                     │ imports
┌────────────────────▼─────────────────────────────────────────────────────┐
│  runtime/  (stateful glue)                                               │
│  • runtime.hitl          — WebHITLBridge — SSE event bus for HITL       │
│  • runtime.credentials   — CredentialService — AES-256 token store      │
│  • runtime.tasks         — TaskStore — in-memory Kanban board            │
│  • runtime.observability — OpenTelemetry setup (Tempo, Prometheus)       │
└────────────────────┬─────────────────────────────────────────────────────┘
                     │ imports
┌────────────────────▼─────────────────────────────────────────────────────┐
│  extensions/  (pluggable capabilities)                                   │
│  • extensions.tools       — WebSurfer, TaskManager, AskHuman, CI        │
│  • extensions.mcp         — McpClient, McpTool, MCP App UIs             │
│  • extensions.skills      — YAML / Markdown skill loader                │
│  • extensions.guardrails  — pre-built safety guardrails                 │
└────────────────────┬─────────────────────────────────────────────────────┘
                     │ imports
┌────────────────────▼─────────────────────────────────────────────────────┐
│  providers/  (swappable external integrations)                           │
│  • providers.llm          — LLM model clients (OpenAI, …)               │
│  • providers.audio        — Audio clients (OpenAI Realtime, …)          │
│  • providers.integrations — Third-party API clients (Spotify, …)        │
└────────────────────┬─────────────────────────────────────────────────────┘
                     │ imports
┌────────────────────▼─────────────────────────────────────────────────────┐
│  core/  (pure logic, no I/O)                                             │
│  • agents        — BaseAgent, ReActAgent, OrchestratorAgent, Flow/Graph  │
│  • tools         — BaseTool, ToolResult                                  │
│  • memory        — BaseMemory, MemoryScope, SessionManager               │
│  • messages      — BaseMessage, agent/client message types               │
│  • context       — ModelContext, context implementations                 │
│  • guardrails    — BaseGuardrail contract + GuardrailResult              │
│  • hooks         — HookManager, HookEvent (lifecycle callbacks)          │
│  • resilience    — RetryPolicy, CircuitBreaker                           │
└──────────────────────────────────────────────────────────────────────────┘
```

### Import rules (enforced by convention)

| Layer | May import from | Must NOT import from |
|---|---|---|
| `core` | stdlib, third-party only | providers, extensions, runtime, server |
| `providers` | core | extensions, runtime, server |
| `extensions` | core, providers | runtime, server |
| `runtime` | core, providers, extensions | server |
| `server` | all layers | (no restrictions) |

---

## Full Directory Tree

```
src/agent_framework/
│
│  __init__.py              <- package root (minimal; importers use explicit paths)
│
├── core/                   <- pure logic, no external I/O
│   ├── agents/
│   │   ├── base_agent.py
│   │   ├── react_agent.py
│   │   ├── orchestrator_agent.py
│   │   ├── flow.py
│   │   ├── graph.py
│   │   └── agent_result.py
│   ├── memory/
│   │   ├── base_memory.py
│   │   ├── postgres_memory.py
│   │   ├── redis_memory.py
│   │   ├── session_manager.py
│   │   ├── unbounded_memory.py
│   │   ├── memory_scope.py
│   │   └── message_serializer.py
│   ├── messages/
│   │   ├── base_message.py
│   │   ├── agent_messages.py
│   │   ├── client_messages.py
│   │   └── _types.py
│   ├── context/
│   │   ├── base_context.py
│   │   └── implementations.py
│   ├── guardrails/
│   │   ├── base_guardrail.py
│   │   ├── prebuilt.py
│   │   └── runner.py
│   ├── hooks.py
│   ├── resilience.py
│   ├── logger.py
│   └── exceptions.py
│
├── providers/              <- swappable external integrations
│   ├── llm/                <- was model_clients/
│   │   ├── base_client.py
│   │   └── openai/
│   │       └── openai_client.py
│   ├── audio/              <- was audio_clients/
│   │   ├── base_audio_client.py
│   │   └── openai/
│   └── integrations/       <- was services/
│       ├── spotify.py
│       └── spotify_auth.py
│
├── extensions/             <- pluggable capabilities
│   ├── tools/              <- was tools/ (base + concrete tools together)
│   │   ├── base_tool.py
│   │   ├── builtin_tools.py
│   │   ├── web_surfer.py
│   │   ├── task_manager_tool.py
│   │   ├── human_input.py
│   │   ├── mcp_client.py
│   │   ├── mcp_tool.py
│   │   ├── mcp_app_tools.py
│   │   └── code_interpreter/
│   ├── skills/             <- was skills/
│   │   ├── loader.py
│   │   ├── manager.py
│   │   └── models.py
│   └── mcp_apps/           <- was mcp_apps/ (HTML templates)
│
├── runtime/                <- stateful glue
│   ├── hitl.py             <- was web_hitl.py
│   ├── credentials.py      <- was credential_service.py
│   ├── tasks/              <- was tasks/
│   │   └── store.py
│   └── observability/      <- was observability/
│       └── telemetry.py
│
├── server/                 <- FastAPI app (unchanged)
│   ├── app.py
│   ├── database.py
│   ├── schemas.py
│   ├── models.py
│   ├── routes/
│   └── services/
│
├── evals/                  <- evaluation harness (unchanged)
├── configs/                <- settings (unchanged)
└── code_interpreter_service/ <- standalone microservice (unchanged)
```

---

## Extension Guides

### How to add a new tool

1. **Create** `src/agent_framework/tools/my_tool.py`:

```python
from __future__ import annotations
from agent_framework.tools.base_tool import BaseTool, ToolResult

class MyTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(
            name="my_tool",
            description="What this tool does — be concise, the LLM reads this.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Input query"},
                },
                "required": ["query"],
            },
        )

    async def execute(self, query: str) -> ToolResult:          # kwargs mirror input_schema
        result = await call_some_api(query)
        return ToolResult(content=str(result), metadata={"source": "my_api"})
```

2. **Register** it in `server/app.py` inside the `lifespan` context manager:

```python
from agent_framework.tools.my_tool import MyTool
# ...
app.state.tools = [
    ...,
    MyTool(),
]
```

3. **Export** (optional) from `extensions/tools/__init__.py` for discoverability.

---

### How to add a new LLM provider

1. **Create** `src/agent_framework/model_clients/myprovider/client.py`:

```python
from __future__ import annotations
from collections.abc import AsyncIterator
from agent_framework.model_clients.base_client import BaseModelClient, StreamEvent

class MyProviderClient(BaseModelClient):
    def __init__(self, model: str, api_key: str) -> None:
        self.model = model
        self._api_key = api_key

    async def stream(self, messages, tools, **kw) -> AsyncIterator[StreamEvent]:
        # call your API, yield StreamEvent objects
        ...
```

2. **Add** an `__init__.py` in the folder exporting `MyProviderClient`.

3. **Export** from `providers/llm/__init__.py`:

```python
from agent_framework.model_clients.myprovider.client import MyProviderClient
```

4. **Use** it:

```python
from agent_framework.providers.llm import MyProviderClient
agent = ReActAgent(model_client=MyProviderClient("my-model", api_key="..."), ...)
```

---

### How to add a new agent type

1. **Create** `src/agent_framework/agents/my_agent.py`:

```python
from __future__ import annotations
from agent_framework.agents.base_agent import BaseAgent
from agent_framework.agents.agent_result import AgentRunResult

class MyAgent(BaseAgent):
    async def run(self, user_message: str, **kw) -> AgentRunResult:
        ...

    async def run_stream(self, user_message: str, **kw):
        ...
        yield event
```

2. **Export** from `core/__init__.py` and from the root `__init__.py`.

---

### How to add a new guardrail

1. **Create** `src/agent_framework/guardrails/my_guardrail.py`:

```python
from __future__ import annotations
from agent_framework.guardrails.base_guardrail import (
    BaseGuardrail, GuardrailContext, GuardrailResult, GuardrailType,
)

class MyGuardrail(BaseGuardrail):
    guardrail_type = GuardrailType.INPUT   # or OUTPUT

    async def run(self, ctx: GuardrailContext) -> GuardrailResult:
        if "forbidden" in ctx.text.lower():
            return GuardrailResult(triggered=True, reason="forbidden keyword")
        return GuardrailResult(triggered=False)
```

2. **Pass** to agent:

```python
agent = ReActAgent(
    ...,
    input_guardrails=[MyGuardrail()],
)
```

---

### How to add a new skill

1. **Create** `skills/my-skill/SKILL.md` with YAML front-matter:

```markdown
---
name: my-skill
description: A short sentence the agent uses to decide when to apply this skill.
tools: [web_surfer]
---

When answering questions about <topic>, always:
1. Check the official documentation first.
2. Cite your sources.
3. Never guess — say "I don't know" and ask the user for clarification.
```

2. The `SkillManager` in `server/app.py` auto-loads every `SKILL.md` it finds,
   so no other code change is required.

---

### How to add a new API route

1. **Create** `src/agent_framework/server/routes/my_feature.py`:

```python
from __future__ import annotations
from fastapi import APIRouter, Request

router = APIRouter(prefix="/my-feature", tags=["my-feature"])

@router.get("/")
async def list_items(request: Request):
    # access DI container: request.app.state.*
    return {"items": []}
```

2. **Mount** in `server/app.py → create_app()`:

```python
from agent_framework.server.routes.my_feature import router as my_feature_router
app.include_router(my_feature_router)
```

---

### How to emit a real-time event to the frontend

From any tool, route, or service:

```python
bridge: WebHITLBridge = request.app.state.bridge   # or passed via context
await bridge.put_event({"type": "my_event", "data": {"key": "value"}})
```

Then handle `my_event` in the SSE switch-case in `ai-chatbot-ui/src/app/page.tsx`.

---

### How to add a new MCP App UI widget

See the **create-mcp-app** skill (`skills/create-mcp-app/SKILL.md`) for detailed
step-by-step instructions including HTML template scaffolding, resource-URI
registration, and frontend `McpAppRenderer` integration.

Short checklist:
1. Add an HTML template to `src/agent_framework/mcp_apps/`.
2. Register the resource URI in your MCP server's tool annotation.
3. The frontend `McpAppRenderer` component renders it automatically.

---

## Data flow — a single chat turn

```
Frontend (page.tsx)
  │  POST /api/chat  { message, thread_id }
  ▼
Next.js API route (app/api/chat/route.ts)  — proxies to backend
  │
  ▼
FastAPI  server/routes/chat.py
  │  sets current_thread_id contextvar
  │  calls agent_service.run_stream(...)
  ▼
agents/react_agent.py  — ReAct loop
  │  1. call model_client.stream(messages, tools)  → text_delta events
  │  2. parse tool_call → look up tool in app.state.tools
  │  3. await tool.execute(**input) → ToolResult
  │  4. loop until no more tool calls
  ▼
WebHITLBridge  (runtime/hitl)
  │  puts SSE events: text_delta, tool_call, tool_result,
  │                   human_input_request, task_updated, …
  ▼
Frontend EventSource  — renders events live
```

---

## Dependency injection — `app.state`

All shared singletons live on `app.state` and are wired in `server/app.py lifespan`.
Routes read them via `request.app.state.*`.

| Attribute | Type | Purpose |
|---|---|---|
| `app.state.tools` | `list[BaseTool]` | All tools available to agents |
| `app.state.bridge` | `WebHITLBridge` | SSE event bus |
| `app.state.memory` | `SessionManager` | Conversation memory |
| `app.state.credential_service` | `CredentialService` | Runtime secrets |
| `app.state.db` | `AsyncEngine` | SQLAlchemy async engine |

---

## Testing

```bash
# Run all tests
uv run pytest

# Run a single module
uv run pytest tests/test_react_agent.py -v

# Run with coverage
uv run pytest --cov=agent_framework --cov-report=html
```

See `.github/instructions/testing.instructions.md` for full testing conventions.
