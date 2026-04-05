# Create A Tool

Every built-in and custom tool follows the same contract: subclass `BaseTool`, declare `name`, `description`, and `input_schema`, then implement `execute()`.

```python
from raavan.core.tools.base_tool import BaseTool, ToolResult


class EchoTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(
            name="echo",
            description="Return the same text back to the agent",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        )

    async def execute(self, *, text: str) -> ToolResult:  # type: ignore[override]
        return ToolResult(content=[{"type": "text", "text": text}])
```

## Important rules

- keep `execute()` async
- use keyword-only args
- return `ToolResult`
- set risk or HITL mode as class-level attributes when needed

## Register the tool

Tools are mounted during application startup and exposed through the agent runtime.

Read more in [Tools And HITL](../concepts/tools-and-hitl.md).