# Actor-Model Migration Guide

**Status**: Source code migrated. Tests and examples need updating.  
**Pass this doc to a worker** to complete the remaining migration (46 failing tests + 14 example notebooks).

---

## What Changed (Summary)

The framework has one agent hierarchy now instead of two.

| Before | After |
|---|---|
| `BaseAgent` → `ReActAgent` (runtime optional, `agent.run()` entry point) | **Deleted** |
| `RuntimeAgent` → `RuntimeAssistantAgent` (actor model, incomplete) | `RuntimeAssistantAgent` deleted; `RuntimeAgent` kept as thin alias |
| _(nothing)_ | `ActorAgent` (new kernel ABC) |
| _(nothing)_ | `AssistantAgent(ActorAgent)` — full ReAct loop inside actor shell |
| _(nothing)_ | `UserProxyAgent(ActorAgent)` — bridges external callers into the fabric |

---

## New Import Paths

```python
# Old
from agent_substrateextensions.agents.react.agent import ReActAgent
from agent_substrate.kernel.agents.base_agent import BaseAgent

# New
from agent_substrateextensions.agents.assistant.agent import AssistantAgent
from agent_substrateextensions.agents.user_proxy.agent import UserProxyAgent
from agent_substrate.kernel.agents.actor import ActorAgent
```

---

## Rule 1: Every agent requires a runtime

`AssistantAgent` takes `runtime: AgentRuntime` as its **second positional argument** (required, not Optional).

```python
# Old
agent = ReActAgent(
    name="bot",
    description="my bot",
    catalog=catalog,
)

# New
from agent_substrate.kernel.runtime._local import LocalRuntime

runtime = LocalRuntime()
await runtime.start()

agent = AssistantAgent(
    "bot",          # positional: name
    runtime,        # positional: runtime (REQUIRED)
    catalog=catalog,
)
await agent.start()  # registers agent with runtime
```

**Context manager shorthand** (preferred for tests/scripts):

```python
async with LocalRuntime() as runtime:
    agent = AssistantAgent("bot", runtime, catalog=catalog)
    await agent.start()
    result = await agent.run("hello")  # compat shim still works
```

---

## Rule 2: Stub agents must implement `on_message()`

`ActorAgent` is abstract — any subclass must implement `on_message()`.

```python
# Old (BaseAgent subclass with abstract run())
class _StubAgent(BaseAgent):
    async def run(self, input_text, **kwargs):
        return AgentRunResult(...)
    async def run_stream(self, input_text, **kwargs):
        yield ...
    def get_system_instructions(self):
        return "test"

# New (ActorAgent subclass with abstract on_message())
class _StubAgent(ActorAgent):
    async def on_message(self, ctx, content):
        return "stub response"
```

If you need `run()` for tests, use `AssistantAgent` with a `MockLLMClient` instead of a stub.

---

## Rule 3: Use `async with LocalRuntime()` in tests

Every test that creates an `AssistantAgent` needs a running runtime.

```python
# Old test pattern
def make_agent(script):
    catalog = AgentCatalogRegistry()
    catalog.register_model("primary", MockLLMClient(script=script))
    return ReActAgent("test", "desc", catalog=catalog, ...)

# New test pattern
async def make_agent(script, *, runtime=None):
    catalog = AgentCatalogRegistry()
    catalog.register_model("primary", MockLLMClient(script=script))
    catalog.register_memory("memory", UnboundedMemory())
    rt = runtime or LocalRuntime()
    if runtime is None:
        await rt.start()
    agent = AssistantAgent("test", rt, catalog=catalog, ...)
    await agent.start()
    return agent

# In test:
async def test_something():
    async with LocalRuntime() as rt:
        agent = await make_agent(script=[text_turn("hi")], runtime=rt)
        result = await agent.run("hello")
    assert result.status == RunStatus.COMPLETED
```

See `tests/extensions/agents/assistant/conftest.py` for the canonical `make_agent` factory.

---

## Rule 4: `load_agent_for_thread()` now requires `runtime`

```python
# Old (runtime was Optional)
agent = await load_agent_for_thread(db, thread_id, model_client=..., tools=..., ...)

# New (runtime required)
agent = await load_agent_for_thread(
    db, thread_id,
    runtime=app.state.runtime,  # REQUIRED
    model_client=..., tools=..., ...
)
```

Tests that mock this function: patch `agent_substrateserver.services.agent_service.create_assistant_agent`
(was `create_react_agent` before).

---

## Rule 5: `OrchestratorAgent` now requires `runtime`

```python
# Old
orchestrator = OrchestratorAgent(
    name="router",
    description="Routes queries",
    model_client=client,
    sub_agents=[agent_a, agent_b],
)

# New — runtime required, sub_agents must be ActorAgent instances
orchestrator = OrchestratorAgent(
    name="router",
    description="Routes queries",
    model_client=client,
    sub_agents=[agent_a, agent_b],
    runtime=runtime,  # REQUIRED
)
```

`sub_agents` type changed from `List[BaseAgent]` → `List[ActorAgent]`. Pass `AssistantAgent` instances.

---

## Failing Tests — Fix Guide

### Group A: Pipeline/integration tests (13 failures)

**Files**: `tests/integration/test_pipeline.py`, `tests/catalog/test_pipelines.py`

**Error**: `TypeError: AssistantAgent.__init__() missing 1 required positional argument: 'runtime'`

**Fix**: Wrap each `_build_agent()` / `AssistantAgent(...)` call in `async with LocalRuntime() as rt:` and pass `runtime=rt`. Example:

```python
# In test_pipeline.py
async def _build_agent(tools=None, system_instructions="..."):
    async with LocalRuntime() as rt:  # wrap
        catalog = AgentCatalogRegistry()
        catalog.register_model("primary", MockLLMClient(...))
        catalog.register_memory("memory", UnboundedMemory())
        for tool in (tools or []):
            catalog.register_tool(tool)
        agent = AssistantAgent("test", rt, catalog=catalog, ...)  # pass rt
        await agent.start()
        return agent  # WARNING: rt stops when `async with` exits!
        # Better: pass rt as a param and manage lifecycle in the test
```

**Better pattern for integration tests** (runtime lives for the whole test):

```python
async def test_echo_then_add_pipeline():
    async with LocalRuntime() as rt:
        agent = await make_agent(script=[...], runtime=rt)
        result = await agent.run("...")
    assert result.status == ...
```

---

### Group B: `test_runtime.py` (7 failures)

**File**: `tests/kernel/runtime/test_runtime.py`

**Errors**: 
- `_StubAgent` can't be instantiated — missing `on_message()` implementation
- Tests assert `agent.runtime is None` — no longer possible (runtime required)
- Tests assert `agent.agent_id is None` — `agent_id` renamed to `agent.id` (property)

**Fix**:

```python
# Replace _StubAgent with:
class _StubAgent(ActorAgent):
    async def on_message(self, ctx, content):
        return "stub"

# Remove test_defaults_are_none (runtime can never be None)
# Replace agent.agent_id with agent.id
# Replace AgentId("type", "key") with the agent's .id property
```

---

### Group C: `test_audit_regressions.py` (4 failures)

**File**: `tests/kernel/runtime/test_audit_regressions.py`

**Error**: Tests call `agent.handle_message()` — this was a `BaseAgent` method that no longer exists.

**Fix**: `handle_message()` is replaced by `on_message()`. Update the test to call `on_message(ctx, content)` directly, or dispatch via `runtime.send_message()`.

---

### Group D: `test_phase3_runtime.py` (1 failure)

**File**: `tests/kernel/runtime/test_phase3_runtime.py`

**Error**: `test_agent_without_runtime_has_none` — tests that `agent.runtime` is `None` when no runtime passed.

**Fix**: Delete this test — runtime can never be `None` in the new model. If the test tests other things too, extract and keep only the non-runtime parts.

---

### Group E: `test_runtime_assistant.py` + `test_runtime_agent_catalog.py` (4 failures)

**Files**: `tests/kernel/runtime/test_runtime_assistant.py`, `tests/kernel/runtime/test_runtime_agent_catalog.py`

**Error**: These tested `RuntimeAssistantAgent` which is deleted.

**Fix**: Update both files to use `AssistantAgent` instead of `RuntimeAssistantAgent`. The `AssistantAgent` has the same `on_message()` interface. Add `async with LocalRuntime() as rt:` wrappers.

---

### Group F: `test_system_instruction_enforcement.py` (11 failures)

**File**: `tests/kernel/test_system_instruction_enforcement.py`

**Error**: Tests were written for `BaseAgent` / `ReActAgent` specific APIs:
- `test_base_agent_subclass_without_get_system_instructions_cannot_instantiate` — no longer meaningful (`get_system_instructions` is gone)
- `test_no_llm_client_rejects_custom_instructions` — `BaseAgent` Pillar D check; `AssistantAgent` always requires a model client
- Constructor argument shapes changed

**Fix**: Rewrite the whole file for `AssistantAgent`. Key things to test:

```python
# test_direct_assignment_raises — still valid
async def test_direct_assignment_raises():
    async with LocalRuntime() as rt:
        agent = AssistantAgent("test", rt, catalog=_catalog_with_llm())
    with pytest.raises(AttributeError):
        agent.system_instructions = "evil"

# test_rewrite_goes_through_mutation_gate — still valid
# test_llm_call_passes_system_instructions_kwarg — still valid
# test_no_system_message_in_messages_passed_to_llm — still valid
# test_injected_system_message_in_memory_is_stripped — still valid

# REMOVE: test_base_agent_subclass_without_get_system_instructions_cannot_instantiate
# REMOVE: test_no_llm_client_rejects_custom_instructions (not applicable)
```

---

### Group G: `test_agent_service.py` (3 failures)

**File**: `tests/server/test_agent_service.py`

**Error**: Tests patch `create_react_agent` (deleted) and don't pass `runtime`.

**Fix**:
1. Change mock patch target: `"agent_substrateserver.services.agent_service.create_react_agent"` → `"agent_substrateserver.services.agent_service.create_assistant_agent"`
2. Add `runtime=MagicMock()` to `load_agent_for_thread()` calls

---

## Examples to Update (14 notebooks in `examples/`)

All examples that import `ReActAgent` or `Agent` need updating. Here is the migration pattern for each:

### Pattern for all examples

```python
# OLD (at top of any notebook cell)
from agent_substrateextensions.agents.react.agent import ReActAgent
# or
from ravi import Agent

# Build agent
catalog = AgentCatalogRegistry()
catalog.register_model("primary", OpenAIClient(model="gpt-4o"))
catalog.register_tool(my_tool)
agent = ReActAgent("my-bot", "description", catalog=catalog)
result = await agent.run("task")

# ─────────────────────────────────────────────

# NEW
from agent_substrateextensions.agents.assistant.agent import AssistantAgent
from agent_substrateextensions.agents.user_proxy.agent import UserProxyAgent
from agent_substrate.kernel.runtime._local import LocalRuntime

# Option A: one-shot (context manager)
async with LocalRuntime() as runtime:
    catalog = AgentCatalogRegistry()
    catalog.register_model("primary", OpenAIClient(model="gpt-4o"))
    catalog.register_tool(my_tool)
    agent = AssistantAgent("my-bot", runtime, catalog=catalog)
    await agent.start()
    result = await agent.run("task")  # compat shim

# Option B: interactive / Console
async with LocalRuntime() as runtime:
    catalog = ...
    agent = AssistantAgent("my-bot", runtime, catalog=catalog)
    await agent.start()
    await Console(agent).interactive()
```

### Notebooks that need updating

Check every `.ipynb` in `examples/` for cells containing:
- `from agent_substrateextensions.agents.react.agent import ReActAgent` → replace with `AssistantAgent`
- `from ravi import Agent` → replace with actor pattern above
- `ReActAgent(name=..., description=..., catalog=..., ...)` → `AssistantAgent(name, runtime, catalog=...)`
- `agent.run(...)` → still works via compat shim, but wrap in `async with LocalRuntime() as runtime:`
- `Console(agent)` → still works (Console detects actor agents automatically)
- `OrchestratorAgent(...)` → add `runtime=runtime` kwarg
- `SequentialFlow([agent_a, agent_b])` → agents must be `AssistantAgent` instances; flows still use `run()` compat shim

---

## Deleted Files (for reference)

| Deleted | Replacement |
|---|---|
| `kernel/agents/base_agent.py` | `kernel/agents/actor.py` (`ActorAgent`) |
| `extensions/agents/react/` (entire dir) | `extensions/agents/assistant/` (`AssistantAgent`) |
| `extensions/agents/runtime/assistant_agent.py` | merged into `AssistantAgent` |
| `extensions/agents/default/agent.py` (`Agent` class) | use `AssistantAgent` directly |
| `shared/execution.create_react_agent` | `shared/execution.create_assistant_agent` |

---

## Quick Reference: Constructor Changes

```python
# ReActAgent (deleted)
ReActAgent(
    name="bot",
    description="my bot",
    *,
    catalog=catalog,                # AgentCatalogRegistry
    system_instructions="...",
    max_iterations=50,
    runtime=None,                   # Optional
    agent_id=None,                  # Optional
    ...
)

# AssistantAgent (new)
AssistantAgent(
    "bot",                          # positional: name
    runtime,                        # positional: runtime (REQUIRED)
    *,
    description="my bot",
    catalog=catalog,                # AgentCatalogRegistry
    system_instructions="...",
    max_iterations=50,
    key="default",                  # replaces agent_id.key
    ...
)
# After construction: await agent.start()
```

---

## Quick Reference: ID Access

```python
# Old
agent.agent_id              # Optional[AgentId] — could be None
agent.agent_id.key          # raises AttributeError if None

# New
agent.id                    # AgentId — always set (computed from name + key)
agent.id.type               # == agent.name
agent.id.key                # == agent.key (default: "default")
```
