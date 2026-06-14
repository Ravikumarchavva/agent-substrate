"""Toolbox — concrete in-memory tool registry.

A mutable name-keyed collection of AnyTool instances. Use this when you need
to share a tool collection across components (e.g. the monolith lifespan
wires tools once and passes them to multiple agents). For simple cases just
pass a plain list[AnyTool] to the agent constructor.
"""

from __future__ import annotations

from ravi.kernel.tools import (
    AnyTool,
    ToolRisk,
    is_hosted_tool,
    is_provider_defined_tool,
)


class Toolbox:
    """In-memory implementation of ToolRegistry (kernel protocol).

    Handles ``Tool``, ``HostedTool``, and ``ProviderDefinedTool`` instances.
    ``schemas()`` produces the wire representation for each:

    - Local tools → ``{"name", "description", "parameters"}`` function schema.
    - Hosted / provider-defined tools → first entry of ``provider_specs`` dict.

    Per-tool ``defer_loading`` is respected: set ``tool.defer_loading = True``
    on any local tool to withhold its full schema until the LLM requests it.
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

    def schemas(self) -> list[dict[str, object]]:
        """Return the full tool list for the LLM."""
        result: list[dict[str, object]] = []
        for t in self._tools.values():
            if is_hosted_tool(t) or is_provider_defined_tool(t):
                specs = t.provider_specs  # type: ignore[union-attr]
                if specs:
                    first_spec = next(iter(specs.values()))
                    result.append(dict(first_spec))
            else:
                entry: dict[str, object] = {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,  # type: ignore[union-attr]
                }
                if getattr(t, "defer_loading", False):
                    entry["defer_loading"] = True
                result.append(entry)
        return result

    def schema_for(self, name: str) -> dict[str, object] | None:
        t = self._tools.get(name)
        if t is None:
            return None
        if is_hosted_tool(t) or is_provider_defined_tool(t):
            specs = t.provider_specs  # type: ignore[union-attr]
            if not specs:
                return None
            return dict(next(iter(specs.values())))
        return {
            "name": t.name,
            "description": t.description,
            "parameters": t.input_schema,  # type: ignore[union-attr]
        }

    def deferred_schemas(
        self, *, include_tool_search: bool = False
    ) -> list[dict[str, object]]:
        """Return all local tool schemas with ``defer_loading=True``.

        Hosted/provider-defined tools are included verbatim (provider-side;
        no deferred flag needed).  Appends a ``tool_search`` sentinel when
        ``include_tool_search=True`` so the LLM can discover tools dynamically.
        """
        result: list[dict[str, object]] = []
        for t in self._tools.values():
            if is_hosted_tool(t) or is_provider_defined_tool(t):
                specs = t.provider_specs  # type: ignore[union-attr]
                if specs:
                    result.append(dict(next(iter(specs.values()))))
            else:
                result.append(
                    {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,  # type: ignore[union-attr]
                        "defer_loading": True,
                    }
                )
        if include_tool_search:
            result.append({"type": "tool_search"})
        return result

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools


__all__ = ["Toolbox"]
