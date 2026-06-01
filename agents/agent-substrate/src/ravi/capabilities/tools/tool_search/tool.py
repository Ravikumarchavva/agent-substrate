"""ToolSearchTool — agent-facing and client-executed tool discovery.

Two modes (chosen by the caller):

  1. **Text mode** (default) — returns a human-readable list of tool
     name + description.  Works with any LLM.

  2. **Schema mode** (``format="schema"``) — returns the full JSON schemas
     of matched tools.  Use this in client-executed tool_search: when
     OpenAI emits a ``tool_search_call``, execute the search and return
     the schemas in a ``tool_search_output`` item.

OpenAI hosted tool search (gpt-5.4+):
    Declare all tools with ``defer_loading: True`` via
    ``ToolRegistry.to_deferred_schemas()`` and add ``{"type": "tool_search"}``
    to the tools list.  The API handles search server-side; your app does not
    need to call this tool at all.

Client-executed tool search:
    Declare ``{"type": "tool_search", "execution": "client"}`` in the tools
    list.  When the model emits a ``tool_search_call``, invoke this tool with
    the query and return the result as a ``tool_search_output`` payload.

Fallback (any model):
    Register as a normal tool.  The model can call it when it needs to
    discover what tools are available.
"""

from __future__ import annotations

import json

from ravi.kernel.content import TextBlock
from ravi.kernel.tools import ToolExecutionResult, ToolRegistry, ToolRisk


class ToolSearchTool:
    """Search the tool registry by keyword.

    Returns matching tools' name and description (text mode, default) or their
    full JSON schemas (schema mode, for client-executed tool_search).
    """

    name = "tool_search"
    description = (
        "Search available tools by keyword. "
        "Returns name and description of matching tools. "
        "Use an empty string to list all tools."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Keyword (case-insensitive). Empty = list all tools.",
            },
            "format": {
                "type": "string",
                "enum": ["text", "schema"],
                "description": (
                    "'text' returns name+description (default). "
                    "'schema' returns full JSON schemas for client-executed tool_search."
                ),
            },
        },
        "required": ["query"],
    }
    risk = ToolRisk.SAFE

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def execute(
        self, *, query: str = "", format: str = "text", **_: object
    ) -> ToolExecutionResult:
        q = query.strip().lower()
        tools = self._registry.all_tools()

        matches = (
            tools
            if not q
            else [t for t in tools if q in t.name.lower() or q in t.description.lower()]
        )

        if format == "schema":
            # Return full schemas — used as tool_search_output payload in
            # client-executed OpenAI tool search.
            schemas = [
                s
                for name in [t.name for t in matches]
                if (s := self._registry.schema_for(name)) is not None
            ]
            if not schemas:
                text = json.dumps({"tools": [], "query": query})
            else:
                text = json.dumps({"tools": schemas, "query": query}, indent=2)
        else:
            # Human-readable text mode (works with any model)
            if not matches:
                text = (
                    f"No tools found matching '{query}'."
                    if q
                    else "No tools registered."
                )
            else:
                header = "Available tools:" if not q else f"Tools matching '{query}':"
                lines = [f"- **{t.name}**: {t.description}" for t in matches]
                text = header + "\n" + "\n".join(lines)

        return ToolExecutionResult(
            name=self.name,
            content=[TextBlock(text=text)],
        )
