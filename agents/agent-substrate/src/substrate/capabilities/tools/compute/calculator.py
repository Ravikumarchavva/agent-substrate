"""CalculatorTool — evaluate arithmetic expressions safely."""

from __future__ import annotations

import ast
import operator
from typing import Callable

from substrate.kernel import TextBlock
from substrate.kernel.tools import ToolExecutionResult

# Whitelisted binary/unary operators. Anything not in these maps is rejected —
# there is no ``eval``, so LLM-controlled input can never reach attribute
# access, calls, names, or comprehensions (the classic
# ``().__class__.__base__.__subclasses__()`` sandbox escape).
_BIN_OPS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

# Guard against trivially abusive exponents (e.g. ``9**9**9``) that would hang
# the process computing an astronomically large integer.
_MAX_POW_EXPONENT = 1000


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("only numeric literals are allowed")
        return node.value
    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"operator {type(node.op).__name__} is not allowed")
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if type(node.op) is ast.Pow and abs(right) > _MAX_POW_EXPONENT:
            raise ValueError("exponent too large")
        return op(left, right)
    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"unary operator {type(node.op).__name__} is not allowed")
        return op(_eval_node(node.operand))
    raise ValueError(f"expression element {type(node).__name__} is not allowed")


def safe_eval(expression: str) -> float:
    """Evaluate an arithmetic expression without executing arbitrary code.

    Only numeric literals and the whitelisted arithmetic operators are
    permitted; everything else (names, calls, attribute access, subscripts)
    raises ``ValueError``.
    """
    tree = ast.parse(expression, mode="eval")
    return _eval_node(tree)


class CalculatorTool:
    """Evaluate a Python arithmetic expression and return the result.

    Example::

        from substrate.capabilities.tools import CalculatorTool
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
            result = safe_eval(expression)
            return ToolExecutionResult(content=[TextBlock(text=str(result))])
        except Exception as exc:
            return ToolExecutionResult(
                content=[TextBlock(text=f"Error: {exc}")],
                is_error=True,
            )
