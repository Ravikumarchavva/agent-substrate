"""Toolbox — concrete in-memory tool registry.

A mutable name-keyed collection of AnyTool instances. Use this when you need
to share a tool collection across components (e.g. the monolith lifespan
wires tools once and passes them to multiple agents). For simple cases just
pass a plain list[AnyTool] to the agent constructor.
"""

from __future__ import annotations

from substrate.kernel.tools import AnyTool, ToolRisk


class Toolbox:
    """In-memory implementation of ToolRegistry (kernel protocol).

    Handles ``Tool``, ``HostedTool``, and ``ProviderDefinedTool`` instances.
    """

    def __init__(self) -> None:
        self._tools: dict[str, AnyTool] = {}

    def add(self, tool: AnyTool) -> None:
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


__all__ = ["Toolbox"]
