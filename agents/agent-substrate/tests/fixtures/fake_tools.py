"""Reusable fake BaseTool subclasses for unit tests.

These tools have deterministic, side-effect-free behaviour so tests
remain hermetic regardless of test order.
"""

from __future__ import annotations

import asyncio
from typing import Any

from substrate.kernel.tools.base_tool import BaseTool, ToolResult, ToolRisk
from substrate.kernel.messages.content import TextBlock


class EchoTool(BaseTool):
    """Returns whatever ``message`` it receives — great for round-trip tests."""

    def __init__(self) -> None:
        super().__init__(
            name="echo",
            description="Echo the provided message back",
            input_schema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
            risk=ToolRisk.SAFE,
        )
        self.call_count = 0

    async def execute(self, message: str, **_: Any) -> ToolResult:  # type: ignore[override]
        self.call_count += 1
        return ToolResult(
            content=[TextBlock(text=f"echo:{message}")],
            app_data={"call_count": self.call_count},
        )


class AddTool(BaseTool):
    """Adds two integers — tests numeric argument passing."""

    def __init__(self) -> None:
        super().__init__(
            name="add",
            description="Add two integers and return the sum",
            input_schema={
                "type": "object",
                "properties": {
                    "a": {"type": "integer"},
                    "b": {"type": "integer"},
                },
                "required": ["a", "b"],
            },
            risk=ToolRisk.SAFE,
        )

    async def execute(self, a: int, b: int, **_: Any) -> ToolResult:  # type: ignore[override]
        return ToolResult(
            content=[TextBlock(text=str(a + b))],
            app_data={"result": a + b},
        )


class FailTool(BaseTool):
    """Always raises — tests error propagation through the agent loop."""

    def __init__(self, error_message: str = "tool exploded") -> None:
        super().__init__(
            name="fail",
            description="A tool that always fails (for error-path tests)",
            input_schema={
                "type": "object",
                "properties": {"reason": {"type": "string"}},
            },
            risk=ToolRisk.SAFE,
        )
        self._error_message = error_message

    async def execute(self, **_: Any) -> ToolResult:  # type: ignore[override]
        raise RuntimeError(self._error_message)


class SlowTool(BaseTool):
    """Sleeps for ``seconds`` — tests timeout enforcement."""

    def __init__(self, seconds: float = 60.0) -> None:
        super().__init__(
            name="slow",
            description="Intentionally slow tool for timeout tests",
            input_schema={
                "type": "object",
                "properties": {"seconds": {"type": "number"}},
            },
            risk=ToolRisk.SAFE,
        )
        self._seconds = seconds

    async def execute(self, **_: Any) -> ToolResult:  # type: ignore[override]
        await asyncio.sleep(self._seconds)
        return ToolResult(content=[TextBlock(text="slow done")])


class CounterTool(BaseTool):
    """Counts how many times it was called — useful for multi-turn tests."""

    def __init__(self) -> None:
        super().__init__(
            name="counter",
            description="Increment an internal counter and return its value",
            input_schema={"type": "object", "properties": {}},
            risk=ToolRisk.SAFE,
        )
        self.count = 0

    async def execute(self, **_: Any) -> ToolResult:  # type: ignore[override]
        self.count += 1
        return ToolResult(
            content=[TextBlock(text=str(self.count))],
            app_data={"count": self.count},
        )
