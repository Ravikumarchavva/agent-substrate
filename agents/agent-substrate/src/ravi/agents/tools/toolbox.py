"""Toolbox — concrete in-memory tool registry.

A mutable name-keyed collection of Tool instances. Use this when you need
to share a tool collection across components (e.g. the monolith lifespan
wires tools once and passes them to multiple agents). For simple cases just
pass a plain list[Tool] to the agent constructor.
"""

from __future__ import annotations

from ravi.kernel.tools import Tool, ToolRisk


class Toolbox:
    """In-memory implementation of ToolRegistry (kernel protocol)."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def add(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def by_risk(self, risk: ToolRisk) -> list[Tool]:
        return [t for t in self._tools.values() if getattr(t, "risk", None) == risk]

    def schemas(self) -> list[dict[str, object]]:
        return [
            {"name": t.name, "description": t.description, "parameters": t.input_schema}
            for t in self._tools.values()
        ]

    def schema_for(self, name: str) -> dict[str, object] | None:
        t = self._tools.get(name)
        if t is None:
            return None
        return {
            "name": t.name,
            "description": t.description,
            "parameters": t.input_schema,
        }

    def deferred_schemas(
        self, *, include_tool_search: bool = False
    ) -> list[dict[str, object]]:
        """Return schemas with ``defer_loading=True`` for all tools, optionally appending a
        ``tool_search`` sentinel so the LLM can discover additional tools dynamically."""
        result: list[dict[str, object]] = [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
                "defer_loading": True,
            }
            for t in self._tools.values()
        ]
        if include_tool_search:
            result.append({"type": "tool_search"})
        return result

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools


__all__ = ["Toolbox"]
