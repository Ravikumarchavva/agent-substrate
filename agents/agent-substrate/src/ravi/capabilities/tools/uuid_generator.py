"""UuidGeneratorTool — generate one or more UUIDs."""

from __future__ import annotations

import uuid

from ravi.kernel import TextBlock
from ravi.kernel.tools import ToolExecutionResult


class UuidGeneratorTool:
    """Generate one or more random UUID v4 values.

    Example::

        from ravi.capabilities.tools import UuidGeneratorTool
        agent = ReActAgent("bot", runtime, model=llm, tools=[UuidGeneratorTool()])
    """

    name = "generate_uuid"
    description = "Generate one or more random UUID v4 strings."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "count": {
                "type": "integer",
                "description": "Number of UUIDs to generate (1–20). Defaults to 1.",
                "minimum": 1,
                "maximum": 20,
            }
        },
        "additionalProperties": False,
    }

    async def execute(self, *, count: int = 1, **_: object) -> ToolExecutionResult:
        count = max(1, min(20, int(count)))
        ids = "\n".join(str(uuid.uuid4()) for _ in range(count))
        return ToolExecutionResult(content=[TextBlock(text=ids)])
