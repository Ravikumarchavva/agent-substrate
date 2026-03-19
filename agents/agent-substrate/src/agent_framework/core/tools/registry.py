"""ToolRegistry — centralised tool catalogue.

Replaces ad-hoc ``List[BaseTool]`` passing throughout the codebase with a
typed, queryable registry that supports lifecycle management.

Usage::

    from agent_framework.core.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register(CalculatorTool())
    registry.register(WebSurferTool(), CalculatorTool())

    # Use in agent construction — registry is iterable
    agent = ReActAgent(..., tools=list(registry))

    # Query by name
    tool = registry.get("calculator")   # BaseTool | None

    # Query by risk
    safe_tools = registry.by_risk(ToolRisk.SAFE)

    # Lifecycle  (call at app startup / shutdown)
    await registry.startup()
    await registry.shutdown()
"""
from __future__ import annotations

import logging
from typing import Dict, Iterable, Iterator, List, Optional

from agent_framework.core.tools.base_tool import BaseTool, ToolRisk

logger = logging.getLogger("agent_framework.tools.registry")


class ToolRegistry:
    """Typed, queryable catalogue of ``BaseTool`` instances.

    Lifecycle
    ---------
    Tools that declare an ``async startup()`` coroutine will be called on
    ``await registry.startup()``, and those with ``async shutdown()`` will be
    called on ``await registry.shutdown()``.  Both are no-ops if the method
    doesn't exist on the tool — this keeps tools lightweight by default.
    """

    def __init__(self) -> None:
        self._tools: Dict[str, BaseTool] = {}

    # ── Registration ─────────────────────────────────────────────────────────

    def register(self, *tools: BaseTool) -> "ToolRegistry":
        """Register one or more tools.  Silently replaces on name collision."""
        for tool in tools:
            if tool.name in self._tools:
                logger.debug("ToolRegistry: replacing existing tool %r", tool.name)
            self._tools[tool.name] = tool
        return self

    def unregister(self, name: str) -> None:
        """Remove a tool by name; no-op if not present."""
        self._tools.pop(name, None)

    # ── Queries ──────────────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[BaseTool]:
        """Return the tool with *name*, or ``None`` if not registered."""
        return self._tools.get(name)

    def all(self) -> List[BaseTool]:
        """Return all registered tools in insertion order."""
        return list(self._tools.values())

    def by_risk(self, risk: ToolRisk) -> List[BaseTool]:
        """Return tools whose ``risk`` attribute matches *risk*."""
        return [t for t in self._tools.values() if getattr(t, "risk", None) == risk]

    def names(self) -> List[str]:
        """Return all registered tool names."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self) -> Iterator[BaseTool]:
        return iter(self._tools.values())

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __repr__(self) -> str:
        names = ", ".join(self._tools) or "(empty)"
        return f"<ToolRegistry [{names}]>"

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def startup(self) -> None:
        """Call ``startup()`` on every tool that defines it."""
        for tool in self._tools.values():
            fn = getattr(tool, "startup", None)
            if callable(fn):
                try:
                    await fn()
                except Exception:
                    logger.exception("ToolRegistry: startup failed for %r", tool.name)

    async def shutdown(self) -> None:
        """Call ``shutdown()`` on every tool that defines it."""
        for tool in self._tools.values():
            fn = getattr(tool, "shutdown", None)
            if callable(fn):
                try:
                    await fn()
                except Exception:
                    logger.exception("ToolRegistry: shutdown failed for %r", tool.name)

    @classmethod
    def from_list(cls, tools: Iterable[BaseTool]) -> "ToolRegistry":
        """Convenience constructor: build a registry from an existing list."""
        registry = cls()
        registry.register(*tools)
        return registry
