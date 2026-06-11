"""CalculatorTool — evaluate arithmetic expressions safely."""

from __future__ import annotations

from ravi.kernel import TextBlock
from ravi.kernel.tools import ToolExecutionResult


class CalculatorTool:
    """Evaluate a Python arithmetic expression and return the result.

    Example::

        from ravi.capabilities.tools import CalculatorTool
        agent = ReActAgent("bot", runtime, model=llm, tools=[CalculatorTool()])
    """

    name = "calculator"
    description = (
        "Evaluate a Python arithmetic expression and return the numeric result."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "Arithmetic expression to evaluate, e.g. '(42 * 3) / 7' or '2 ** 10'.",
            }
        },
        "required": ["expression"],
        "additionalProperties": False,
    }

    async def execute(self, *, expression: str, **_: object) -> ToolExecutionResult:
        try:
            result = eval(expression, {"__builtins__": {}})  # noqa: S307
            return ToolExecutionResult(content=[TextBlock(text=str(result))])
        except Exception as exc:
            return ToolExecutionResult(
                content=[TextBlock(text=f"Error: {exc}")],
                is_error=True,
            )
