"""Tool execution contracts."""

from __future__ import annotations

from enum import Enum
from uuid import uuid4

from typing import Protocol

from pydantic import BaseModel, Field

from ravi.kernel.content import ContentBlock, JsonObject, content_blocks_to_str


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


class ToolCallRequest(BaseModel):
    """A request to execute a named tool."""

    name: str
    arguments: JsonObject = Field(default_factory=dict)
    call_id: str = Field(default_factory=lambda: str(uuid4()))

    model_config = {"frozen": True}


class ToolExecutionResult(BaseModel):
    """Result from a single tool execution."""

    call_id: str = ""
    name: str = ""
    content: list[ContentBlock] = Field(default_factory=list)
    is_error: bool = False
    metadata: JsonObject = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True, "frozen": False}

    @property
    def text(self) -> str:
        """Human-readable lowering of all content blocks."""
        return content_blocks_to_str(self.content)


class Tool(Protocol):
    """Contract every tool must satisfy.

    ``risk`` is optional — defaults to ``ToolRisk.SAFE`` when absent.
    """

    name: str
    description: str
    input_schema: dict[str, object]

    async def execute(self, **kwargs: object) -> ToolExecutionResult: ...


class ToolRegistry:
    """Simple name-keyed registry for Tool instances.

    Replaces the old catalog variable. Tags, categories, and aliases
    passed to register() are silently ignored — keep metadata in the
    tool itself via ``risk`` and ``description``.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool, **_kwargs: object) -> None:
        """Register a tool under its name."""
        self._tools[tool.name] = tool

    # alias used in lifespan wiring
    def register_tool(self, tool: Tool, **kwargs: object) -> None:
        self.register(tool, **kwargs)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    # alias used in pipeline validation
    def get_tool(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def by_risk(self, risk: ToolRisk) -> list[Tool]:
        return [
            t for t in self._tools.values() if getattr(t, "risk", ToolRisk.SAFE) == risk
        ]

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def schema_for(self, name: str) -> dict[str, object] | None:
        """Return the full tool schema dict for ``name``, or None if not found.

        Used by client-executed tool search: after receiving a tool_search_call
        the application calls this to get the full schema to return in
        tool_search_output.
        """
        t = self._tools.get(name)
        if t is None:
            return None
        return {
            "name": t.name,
            "description": t.description,
            "parameters": t.input_schema,
        }

    def to_deferred_schemas(
        self, *, include_tool_search: bool = True
    ) -> list[dict[str, object]]:
        """Return tool schemas for OpenAI hosted tool search (gpt-5.4+).

        All tools are marked ``defer_loading: true`` so only their name and
        description are loaded into the model context upfront.  The full
        parameter schema is injected on demand when the model calls
        ``tool_search``.

        Set ``include_tool_search=False`` to omit the sentinel (e.g. when
        adding it manually alongside namespace definitions).
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

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools


__all__ = ["ToolRisk", "ToolCallRequest", "ToolExecutionResult", "Tool", "ToolRegistry"]
