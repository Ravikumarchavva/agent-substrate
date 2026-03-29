# Design Patterns in Raavan

This document catalogues every GoF and architectural pattern used in the codebase,
organized by category (Creational, Structural, Behavioral), with the canonical file
location and the _why_ behind each decision.

---

## Creational Patterns

Concerned with object creation and initialization.

### 1. Factory Method — `create_file_store`

**Files:** `src/raavan/core/storage/factory.py`

Reads `FILE_STORE_BACKEND` from settings and returns a concrete `FileStore`
subclass (`LocalFileStore`, `S3FileStore`, …), optionally wrapped with
`EncryptedFileStore`.

```python
store = create_file_store(settings)  # caller never imports LocalFileStore
```

**Why:** Lets configuration drive the storage backend without scattering
`if backend == "s3":` logic across the codebase.

---

### 2. Registry — `CapabilityRegistry`

**Files:** `src/raavan/core/tools/catalog.py`

Central lookup for both `BaseTool` instances and skill metadata objects.
Supports alias resolution, category browsing, and multi-signal lexical search.

```python
catalog = CapabilityRegistry()
catalog.register_tool(CalculatorTool(), category="productivity", tags=["math"])
entry = catalog.get("calculator")
results = catalog.search("do some math")
```

**Why:** Decouples the agent loop from concrete tool imports. The agent only
knows `CapabilityRegistry`; adding a new tool never touches agent code.

**Key rules:**
- `all_tools()` guarantees `List[BaseTool]` (no `None` elements).
- `startup()` / `shutdown()` delegate to each tool that defines those hooks.
- Search is always global — category boosts scores but never filters.

---

### 3. Convention-based Discovery — `CatalogScanner`

**Files:** `src/raavan/catalog/_scanner.py`

Walks `catalog/tools/`, `catalog/skills/`, and `catalog/connectors/` looking
for named conventions (`tool.py`, `SKILL.md`, `connector.py`). Loads the first
`BaseTool` subclass found in each `tool.py` without the caller needing to know
the concrete class name.

**Key fix applied:** `_to_module_path()` anchors on the **last** `raavan` path
segment (not the first) to handle the `src/raavan` layout on Windows.

---

## Structural Patterns

Concerned with object composition and relationships between entities.

### 4. Abstract Base Class / Interface

**Files:**
- `src/raavan/core/storage/base.py` — `FileStore` + `FileRef` value object
- `src/raavan/core/agents/base_agent.py` — `BaseAgent`
- `src/raavan/core/guardrails/base_guardrail.py` — `BaseGuardrail`

Each ABC declares the contract its implementations must satisfy.
`FileRef` is a frozen `@dataclass` — an immutable **value object** returned
after every `put()`, making receipts safe to cache/compare.

---

### 5. Adapter — `MCPTool` / `MCPClient`

**Files:** `src/raavan/integrations/mcp/client.py`, `src/raavan/integrations/mcp/tool.py`

`MCPClient` discovers remote MCP tools over stdio or SSE transport.
`MCPTool` adapts each remote tool to the local `BaseTool` interface, exposing
three schema formats:

| Method | Returns | When to use |
|---|---|---|
| `get_schema()` | `Tool` (framework) | Pass to `ReActAgent(tools=[...])` |
| `get_openai_schema()` | `dict` (OpenAI) | Pass to `client.generate(tools=[...])` |
| `get_mcp_schema()` | `dict` (MCP wire) | MCP protocol / debugging |

---

### 6. Proxy — `AdapterProxy` / `ChainRuntime`

**Files:** `src/raavan/catalog/_chain_runtime.py`

`AdapterProxy` wraps a `BaseTool` as an async callable for LLM-authored scripts.
Transparently stores large results as `DataRef` pointers to avoid context bloat.

```python
await adapters.calculator(expression="2+2")
# ↑ ChainRuntime builds this namespace; caller never imports the tool class
```

`ChainRuntime` is the **director** that assembles the proxied namespace at
runtime from the live `CapabilityRegistry`.

---

### 7. Decorator — `EncryptedFileStore`

**Files:** `src/raavan/core/storage/encrypted.py`

Wraps any `FileStore` to add transparent AES-GCM envelope encryption without
subclassing. This is the **Decorator** (GoF §4.4) pattern — same interface,
added behaviour.

```python
store = EncryptedFileStore(LocalFileStore(root), key=master_key)
# caller uses store.put() / store.get() — encryption is invisible
```

---

## Behavioral Patterns

Concerned with object collaboration and responsibility distribution.

### 8. Template Method — `BaseTool`

**Files:** `src/raavan/core/tools/base_tool.py`

`BaseTool` defines the invariant steps of tool execution in `run()` — input
validation, then dispatch — and leaves the variant step to subclasses via the
abstract `execute()` hook.

```python
async def run(self, **kwargs) -> ToolResult:
    self._validate_input(kwargs)   # invariant step (always runs)
    return await self.execute(**kwargs)  # variant — subclass fills in

@abstractmethod
async def execute(self, **kwargs) -> ToolResult: ...
```

**Why:** Guarantees every tool's kwargs are JSON-schema validated before any
subclass code runs. Subclasses cannot skip validation or forget to call a
`super()` chain.

**Convention:** All catalog tools add `# type: ignore[override]` when their
`execute()` uses keyword-only parameters:

```python
async def execute(self, *, url: str, method: str = "GET") -> ToolResult:  # type: ignore[override]
```

---

### 9. Strategy — `ToolRisk` / `HitlMode`

**Files:** `src/raavan/core/tools/base_tool.py`

`ToolRisk` (SAFE / SENSITIVE / CRITICAL) and `HitlMode` (BLOCKING /
CONTINUE_ON_TIMEOUT / FIRE_AND_CONTINUE) are Strategy enums: they select
a _behaviour_ at definition time without modifying the tool class itself.

```python
class EmailSenderTool(BaseTool):
    risk = ToolRisk.CRITICAL
    hitl_mode = HitlMode.BLOCKING
```

`ToolRisk.color` drives the badge rendered in the UI without the frontend
needing to import the Python enum.

---

### 10. Observer / Event Bus — `EventBus` (SSE, monolith) and `shared.events` (microservices)

**Files:**
- `src/raavan/server/sse/events.py` — `EventBus` + typed event dataclasses (monolith)
- `src/raavan/shared/events/bus.py` — Redis pub/sub `EventBus` (microservices)
- `src/raavan/shared/events/types.py` — domain event factory functions

The agent loop is the publisher; the SSE route is the subscriber.

**Critical rule — always use factory functions:**

```python
# ✅ correct
await bus.publish(workflow_started(job_id=run.job_id, run_id=run.id))

# ❌ wrong — never build dicts manually
await bus.publish({"event_type": "workflow.started", ...})
```

Typed SSE events (`TextDeltaEvent`, `ToolCallEvent`, etc.) carry a `to_dict()`
method so the SSE route never constructs JSON by hand.

---

### 11. Pipeline Builder — `PipelineRunner`

**Files:** `src/raavan/core/pipelines/runner.py`

Turns a JSON graph (from the visual builder) into live objects by topology
detection. Priority order: `while` → `condition` → `router` → `agent`.

---

### 12. Protocol / Duck Typing — `PromptEnricher`

**Files:** `src/raavan/core/agents/base_agent.py`

`PromptEnricher` is a `@runtime_checkable` Protocol. `SkillManager` implements
it without inheriting from it, keeping `core/` free of imports from
`integrations/`.

```python
@runtime_checkable
class PromptEnricher(Protocol):
    def inject_into_prompt(self, system_prompt: str) -> str: ...
```

---

### 13. ReAct Agent Loop — Think → Act → Observe

**Files:** `src/raavan/core/agents/react_agent.py`

The ReAct agent iterates: receive LLM output → if tool_call, invoke tool and
feed result back → repeat until `completion` or `max_iterations`.

```
User input
    ↓
[LLM generates] → text_delta  (streaming via EventBus)
    ↓ (if tool_call)
[Tool executes] → tool_result → fed back to LLM context
    ↓ (repeat)
[LLM generates completion]
```

Guardrails run at three injection points: INPUT (before LLM), OUTPUT (after
LLM), TOOL_CALL (before tool execution). All guardrails in each phase run
concurrently via `asyncio.gather`.

---

## Architectural Patterns

Cross-cutting concerns and system-wide organization.

### 14. Dependency Injection via `app.state`

**Files:** `src/raavan/server/app.py`, every `services/<name>/app.py`

FastAPI `app.state` is the DI container. All shared objects (model client,
registry, bridges, DB session factory) are mounted in the `lifespan` context
manager and read in route handlers via `request.app.state.*`.

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.model_client = OpenAIClient(...)
    app.state.registry = CapabilityRegistry(...)
    yield
    # teardown here

# Route handler
def chat(request: Request):
    client = request.app.state.model_client
```

**Why:** No global singletons. Every dependency is replaced in tests by
setting `app.state.*` before the test runs.

---

## Anti-patterns to Avoid

| Anti-pattern | Why it's banned |
|---|---|
| `app.state` global singleton via `import` | Breaks test isolation |
| Manual event dict construction | Bypasses schema contract; use factory functions |
| `BaseTool.close()` / sync memory methods | Memory is always async; no `close()` exists |
| `from agent_framework...` imports | Old package name; always `from raavan...` |
| Inline `if backend == "s3":` in non-factory code | That logic belongs in `create_file_store` |
| `pip install` | Always use `uv` |
