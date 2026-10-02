"""Toolbox — concrete in-memory tool registry.

A mutable name-keyed collection of AnyTool instances. Use this when you need
to share a tool collection across components (e.g. the monolith lifespan
wires tools once and passes them to multiple agents). For simple cases just
pass a plain list[AnyTool] to the agent constructor.
"""

from __future__ import annotations

from substrate.types.errors import ToolDeclarationError
from substrate.tools.protocols import AnyTool, ToolRisk, is_hosted_tool


def check_declaration(tool: object) -> None:
    """Refuse a locally-executed tool that does not declare ``risk`` and ``idempotent``.

    A provider-hosted tool runs on the provider's side and has no local effect to classify.
    """
    if is_hosted_tool(tool):
        return
    problems: list[str] = []
    if not isinstance(getattr(tool, "risk", None), ToolRisk):
        problems.append(f"risk must be a ToolRisk, got {getattr(tool, 'risk', None)!r}")
    if not isinstance(getattr(tool, "idempotent", None), bool):
        problems.append(f"idempotent must be True or False, got {getattr(tool, 'idempotent', None)!r}")
    if problems:
        raise ToolDeclarationError(str(getattr(tool, "name", tool)), tuple(problems))


class Toolbox:
    """In-memory implementation of ToolRegistry (kernel protocol).

    Handles ``Tool``, ``HostedTool``, and ``ProviderDefinedTool`` instances.
    """

    def __init__(self) -> None:
        self._tools: dict[str, AnyTool] = {}

    def add(self, tool: AnyTool) -> None:
        check_declaration(tool)
        self._tools[tool.name] = tool

    def get(self, name: str) -> AnyTool | None:
        return self._tools.get(name)

    def all(self) -> list[AnyTool]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def by_risk(self, risk: ToolRisk) -> list[AnyTool]:
        return [t for t in self._tools.values() if getattr(t, "risk", None) == risk]

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools


__all__ = ["Toolbox", "check_declaration"]
