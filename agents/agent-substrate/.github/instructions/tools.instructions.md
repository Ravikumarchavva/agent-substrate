---
applyTo: "src/agent_framework/tools/**"
description: Conventions for writing agent tools
---

# Tool Authoring Rules

## Every tool MUST:
1. Subclass `BaseTool` from `agent_framework.tools.base_tool`.
2. Call `super().__init__(name, description, input_schema)` in `__init__`.
3. Implement `async def execute(self, **kwargs) -> ToolResult`.
4. Document each `input_schema` property with a `"description"` field.
5. Return `ToolResult(content=..., metadata={...})`.

## required=[] vs optional fields
Mark genuinely optional parameters by omitting them from `"required"`.
Validate presence inside `execute()` and raise `ValueError` for bad input.

## SSE-emitting tools
If the tool needs to push live UI updates, accept an `event_emitter` arg:
```python
def __init__(self, event_emitter: Optional[Callable] = None):
    self._emit = event_emitter

async def execute(self, **kwargs):
    if self._emit:
        await self._emit({"type": "my_event", "data": {...}})
```
`event_emitter` is `bridge.put_event` wired in `server/app.py` lifespan.

## Thread-safety for concurrent requests
If the tool needs per-conversation state, use `contextvars.ContextVar`:
```python
import contextvars
my_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("my_ctx", default="default")
```
Set it in `server/routes/chat.py` before `agent.run_stream()`.

## Registration
Add the tool instance to `app.state.tools` in `server/app.py` lifespan.
Tool names must be lowercase snake_case and globally unique.

## Testing
Test tools by calling `await tool.execute(**params)` directly in pytest-asyncio tests.
No need to spin up FastAPI for tool unit tests.
