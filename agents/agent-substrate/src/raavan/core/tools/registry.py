"""ToolRegistry — centralised tool catalogue.

Replaces ad-hoc ``List[BaseTool]`` passing throughout the codebase with a
typed, queryable registry that supports lifecycle management.

Usage::

    from raavan.core.tools.registry import ToolRegistry

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
import re
from typing import Any, Dict, Iterable, Iterator, List, Optional

from raavan.core.tools.base_tool import BaseTool, ToolRisk

logger = logging.getLogger("raavan.tools.registry")

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "at",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "me",
    "of",
    "on",
    "or",
    "please",
    "show",
    "the",
    "to",
    "what",
    "with",
}


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

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        exclude_names: Optional[set[str]] = None,
    ) -> List[BaseTool]:
        """Return the best matching tools for a free-text query.

        Uses a lightweight lexical scorer over tool name, description, and
        input-schema property names/descriptions. This avoids sending the full
        tool catalogue to the model on every turn.
        """
        normalized = query.strip().lower()
        if not normalized:
            return []

        excludes = exclude_names or set()
        ranked: List[tuple[int, BaseTool]] = []
        for tool in self._tools.values():
            if tool.name in excludes:
                continue
            score = self._score_tool(tool, normalized)
            if score > 0:
                ranked.append((score, tool))

        ranked.sort(key=lambda item: (-item[0], item[1].name))
        return [tool for _, tool in ranked[: max(1, limit)]]

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        return [
            token
            for token in re.split(r"[^a-z0-9_]+", text.lower())
            if token and len(token) >= 3 and token not in _STOPWORDS
        ]

    def _tool_search_text(self, tool: BaseTool) -> str:
        properties = tool.input_schema.get("properties", {})
        prop_bits: List[str] = []
        for name, prop in properties.items():
            if isinstance(prop, dict):
                prop_bits.append(name)
                description = prop.get("description")
                if isinstance(description, str):
                    prop_bits.append(description)
        return " ".join([tool.name, tool.description, *prop_bits]).lower()

    def _score_tool(self, tool: BaseTool, normalized_query: str) -> int:
        tokens = self._tokenize(normalized_query)
        haystack = self._tool_search_text(tool)
        score = 0

        if tool.name == normalized_query:
            score += 120
        if normalized_query in tool.name:
            score += 60
        if normalized_query in haystack:
            score += 30

        for token in tokens:
            if token == tool.name:
                score += 25
            elif token in tool.name:
                score += 12

            if token in haystack:
                score += 6

        return score

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
            fn: Any = getattr(tool, "startup", None)
            if callable(fn):
                try:
                    await fn()  # type: ignore[misc]
                except Exception:
                    logger.exception("ToolRegistry: startup failed for %r", tool.name)

    async def shutdown(self) -> None:
        """Call ``shutdown()`` on every tool that defines it."""
        for tool in self._tools.values():
            fn: Any = getattr(tool, "shutdown", None)
            if callable(fn):
                try:
                    await fn()  # type: ignore[misc]
                except Exception:
                    logger.exception("ToolRegistry: shutdown failed for %r", tool.name)

    @classmethod
    def from_list(cls, tools: Iterable[BaseTool]) -> "ToolRegistry":
        """Convenience constructor: build a registry from an existing list."""
        registry = cls()
        registry.register(*tools)
        return registry
