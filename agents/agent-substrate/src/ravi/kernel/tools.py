"""Tool contracts — the Tool Protocol, ToolRisk, and the Toolbox."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from ravi.kernel.message import ToolCallRequest, ToolExecutionResult


@dataclass(frozen=True)
class ToolUI:
    """Declares that a tool renders through an MCP-App UI resource.

    Mirrors the MCP Apps tool ``_meta.ui`` object so external MCP App servers
    interoperate and the host can preload the resource before the call:

    - ``resource_uri``    the ``ui://name`` resource that renders this tool.
    - ``csp``             allowed external origins, keyed by MCP CSP category
                          (``connectDomains`` / ``resourceDomains`` /
                          ``frameDomains`` / ``baseUriDomains``).
    - ``permissions``     sandbox capabilities to request
                          (``camera`` / ``microphone`` / ``geolocation`` /
                          ``clipboardWrite``).
    - ``prefers_border``  host hint to draw a visual boundary.

    Optional on a tool — its absence means the tool has no UI.  Internal tools
    may skip the declaration and return a ``UIResourceBlock`` directly.
    """

    resource_uri: str
    csp: dict[str, list[str]] | None = None
    permissions: tuple[str, ...] = field(default_factory=tuple)
    prefers_border: bool = False


class ToolRisk(str, Enum):
    """Risk classification for a tool.

    SAFE     — no side-effects; execute without approval.
    HIGH     — external side-effects (email, DB write); require approval when
               an ApprovalHandler is configured.
    CRITICAL — destructive / irreversible; always require approval.
    """

    SAFE = "safe"
    HIGH = "high"
    CRITICAL = "critical"


class Tool(Protocol):
    """Contract every tool must satisfy.

    ``risk`` is optional — defaults to ``ToolRisk.SAFE`` when absent.
    ``ui`` is optional — a ``ToolUI`` declaration when the tool renders through
    an MCP-App resource; its absence means the tool has no UI.
    """

    name: str
    description: str
    input_schema: dict[str, object]

    async def execute(self, **kwargs: object) -> ToolExecutionResult: ...


class Toolbox:
    """A name-keyed collection of Tool instances with schema-building helpers.

    Use this when you need to share a tool collection across components
    (e.g. the monolith lifespan wires tools once and passes them to multiple
    agents). For simple cases just pass a plain ``list[Tool]`` to the agent.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def add(self, tool: Tool) -> None:
        """Register a tool under its name."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """Return the tool with *name*, or None if not registered."""
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        """Return all registered tools."""
        return list(self._tools.values())

    def names(self) -> list[str]:
        """Return all registered tool names."""
        return list(self._tools.keys())

    def schemas(self) -> list[dict[str, object]]:
        """Full tool schemas for LLM function-calling."""
        return [
            {"name": t.name, "description": t.description, "parameters": t.input_schema}
            for t in self._tools.values()
        ]

    def schema_for(self, name: str) -> dict[str, object] | None:
        """Return the full schema dict for *name*, or None if not found.

        Used after a tool_search_call to inject the full parameter schema.
        """
        t = self._tools.get(name)
        if t is None:
            return None
        return {"name": t.name, "description": t.description, "parameters": t.input_schema}

    def deferred_schemas(self, *, include_tool_search: bool = True) -> list[dict[str, object]]:
        """Deferred schemas for OpenAI hosted tool search (gpt-5.4+).

        All tools are marked ``defer_loading: true`` — only name and
        description are sent upfront; the full schema is injected on
        demand when the model calls ``tool_search``.
        """
        schemas: list[dict[str, object]] = [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
                "defer_loading": True,
            }
            for t in self._tools.values()
        ]
        if include_tool_search:
            schemas.append({"type": "tool_search"})
        return schemas

    def by_risk(self, risk: ToolRisk) -> list[Tool]:
        """Return all tools with the given risk level."""
        return [t for t in self._tools.values() if getattr(t, "risk", ToolRisk.SAFE) == risk]

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools


# Re-exported here for import convenience — canonical home is kernel.message.
__all__ = [
    "ToolRisk",
    "ToolUI",
    "Tool",
    "Toolbox",
    # message payloads re-exported so tools can do: from ravi.kernel.tools import ToolExecutionResult
    "ToolCallRequest",
    "ToolExecutionResult",
]
