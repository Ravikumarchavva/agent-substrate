"""Invariant register — tool safety is declared, never assumed (row I22).

The audit found a tool with no ``risk`` silently treated as ``SAFE`` — which made every
tool from an external MCP server approval-free — and a tool whose risk was the string
``"sensitive"``, a level that does not exist. A tool that does not say how dangerous it
is, or whether a call may be repeated, is refused when it is registered.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from typing import Any

import pytest

from substrate.types import TextBlock
from substrate.types.errors import ToolDeclarationError
from substrate.tools import ToolExecutionResult, ToolRisk
from substrate.tools import Toolbox


def _tool(**declared: Any) -> object:
    attrs: dict[str, Any] = {
        "name": "probe",
        "description": "d",
        "input_schema": {"type": "object", "properties": {}},
        "execute": lambda self, **kw: ToolExecutionResult(
            name="probe", content=[TextBlock(text="ok")]
        ),
        **declared,
    }
    return type("Probe", (), attrs)()


def test_i22_a_tool_without_a_risk_is_refused() -> None:
    with pytest.raises(ToolDeclarationError, match="risk"):
        Toolbox().add(_tool(idempotent=True))  # type: ignore[arg-type]


def test_i22_a_tool_without_an_idempotency_declaration_is_refused() -> None:
    with pytest.raises(ToolDeclarationError, match="idempotent"):
        Toolbox().add(_tool(risk=ToolRisk.SAFE))  # type: ignore[arg-type]


def test_i22_a_risk_that_is_not_a_tool_risk_is_refused() -> None:
    """``"sensitive"`` is not a level. A string that merely looks right is how a tool ends up
    outside the approval ordering."""
    with pytest.raises(ToolDeclarationError, match="risk"):
        Toolbox().add(_tool(risk="sensitive", idempotent=True))  # type: ignore[arg-type]


def test_i22_a_fully_declared_tool_is_accepted() -> None:
    box = Toolbox()
    box.add(_tool(risk=ToolRisk.SAFE, idempotent=True))  # type: ignore[arg-type]
    assert "probe" in box


def _shipped_tool_classes() -> list[type]:
    """Every class under ``integrations.tools`` that is a tool: it has an async ``execute``
    and either an ``input_schema`` of its own or a name ending in ``Tool``."""
    import substrate.integrations.tools as package

    found: list[type] = []
    for module_info in pkgutil.walk_packages(
        package.__path__, prefix=f"{package.__name__}."
    ):
        try:
            module = importlib.import_module(module_info.name)
        except ImportError:  # an optional dependency this environment lacks
            continue
        source_path = getattr(module, "__file__", None)
        if not source_path:
            continue
        for node in ast.parse(open(source_path, encoding="utf-8").read()).body:
            if not isinstance(node, ast.ClassDef):
                continue
            has_execute = any(
                isinstance(b, ast.AsyncFunctionDef) and b.name == "execute"
                for b in node.body
            )
            declares_shape = any(
                (
                    isinstance(b, ast.AnnAssign)
                    and getattr(b.target, "id", "") in ("input_schema",)
                )
                or (
                    isinstance(b, ast.Assign)
                    and any(
                        getattr(t, "id", "") in ("input_schema",) for t in b.targets
                    )
                )
                for b in node.body
            )
            if has_execute and (declares_shape or node.name.endswith("Tool")):
                found.append(getattr(module, node.name))
    return found


def test_i22_every_shipped_tool_declares_its_risk_and_idempotency() -> None:
    classes = _shipped_tool_classes()
    assert len(classes) >= 15, (
        f"the scan found only {len(classes)} tools; it has stopped finding them"
    )
    # MCPTool takes both at construction, with a deny-by-default value: checked separately below.
    undeclared = {
        cls.__qualname__: (getattr(cls, "risk", None), getattr(cls, "idempotent", None))
        for cls in classes
        if cls.__name__ != "MCPTool"
        and not (
            isinstance(getattr(cls, "risk", None), ToolRisk)
            and isinstance(getattr(cls, "idempotent", None), bool)
        )
    }
    assert not undeclared, (
        f"tools that do not declare risk and idempotent: {undeclared}"
    )


def test_i22_a_tool_from_an_mcp_server_needs_approval_unless_the_operator_says_otherwise() -> (
    None
):
    from substrate.integrations.tools.mcp.tool import MCPTool

    server_tool = MCPTool(
        client=object(), name="rm", description="d", input_schema={"type": "object"}
    )  # type: ignore[arg-type]
    assert server_tool.risk is ToolRisk.HIGH and server_tool.idempotent is False

    vouched = MCPTool(
        client=object(),
        name="read",
        description="d",
        input_schema={"type": "object"},
        risk=ToolRisk.SAFE,
        idempotent=True,
    )  # type: ignore[arg-type]
    assert vouched.risk is ToolRisk.SAFE and vouched.idempotent is True
