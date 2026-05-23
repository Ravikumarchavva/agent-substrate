"""Safe expression evaluator for pipeline conditions.

Replaces eval() with a recursive AST walker that supports only a strict
safe subset of Python expressions:
  - Literals: string, int, float, bool, None
  - Variable references resolved from a caller-supplied context dict
  - Comparisons: ==  !=  <  <=  >  >=  in  not in
  - Boolean operators: and  or  not

No function calls, no attribute access, no subscripts, no imports.
Any expression that uses an unsupported construct evaluates to False.
"""

from __future__ import annotations

import ast
import logging
from typing import Any

logger = logging.getLogger(__name__)


def safe_eval(expr: str, ctx: dict[str, Any]) -> bool:
    """Evaluate *expr* against *ctx*.  Returns False on any error or unsafe usage."""
    if not expr.strip():
        return False
    try:
        tree = ast.parse(expr.strip(), mode="eval")
        return bool(_eval(tree.body, ctx))
    except _UnsafeExpr as exc:
        logger.debug("Pipeline condition rejected (unsafe): %s — %s", expr, exc)
        return False
    except Exception as exc:
        logger.debug("Pipeline condition eval error: %s — %s", expr, exc)
        return False


class _UnsafeExpr(Exception):
    """Raised when the expression uses a construct we don't allow."""


def _eval(node: ast.AST, ctx: dict[str, Any]) -> Any:
    """Recursively evaluate an AST node against *ctx*."""
    # Literal constants: strings, numbers, booleans, None
    if isinstance(node, ast.Constant):
        return node.value

    # Variable lookup — only names present in ctx are allowed
    if isinstance(node, ast.Name):
        if node.id in ctx:
            return ctx[node.id]
        # Allow bare True/False/None even if not in ctx
        if node.id == "True":
            return True
        if node.id == "False":
            return False
        if node.id == "None":
            return None
        raise _UnsafeExpr(f"Unknown variable: {node.id!r}")

    # Boolean operators: and / or
    if isinstance(node, ast.BoolOp):
        values = [_eval(v, ctx) for v in node.values]
        if isinstance(node.op, ast.And):
            return all(values)
        if isinstance(node.op, ast.Or):
            return any(values)
        raise _UnsafeExpr(f"Unsupported BoolOp: {type(node.op).__name__}")

    # Unary not
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            return not _eval(node.operand, ctx)
        raise _UnsafeExpr(f"Unsupported UnaryOp: {type(node.op).__name__}")

    # Comparison operators
    if isinstance(node, ast.Compare):
        left = _eval(node.left, ctx)
        for op, right_node in zip(node.ops, node.comparators):
            right = _eval(right_node, ctx)
            if isinstance(op, ast.Eq):
                result = left == right
            elif isinstance(op, ast.NotEq):
                result = left != right
            elif isinstance(op, ast.Lt):
                result = left < right
            elif isinstance(op, ast.LtE):
                result = left <= right
            elif isinstance(op, ast.Gt):
                result = left > right
            elif isinstance(op, ast.GtE):
                result = left >= right
            elif isinstance(op, ast.In):
                result = left in right
            elif isinstance(op, ast.NotIn):
                result = left not in right
            else:
                raise _UnsafeExpr(f"Unsupported comparison op: {type(op).__name__}")
            if not result:
                return False
            left = right  # support chained comparisons: a < b < c
        return True

    raise _UnsafeExpr(f"Unsupported expression node: {type(node).__name__}")
