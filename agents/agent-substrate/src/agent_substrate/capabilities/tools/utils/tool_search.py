"""ToolSearchTool — search available tools by keyword.

Register this as a normal tool and the agent can discover what tools it has
and how to call them — name, description, and full parameter list — without
needing to know upfront.

    tools = [CalculatorTool(), WebSearchTool(), WikipediaTool()]
    agent = ReActAgent("bot", runtime, model=llm,
                       tools=[*tools, ToolSearchTool(tools)])
"""

from __future__ import annotations

import json

from agent_substrate.kernel import Tool, TextBlock
from agent_substrate.kernel.tools import ToolExecutionResult, ToolRisk


def _param_summary(schema: dict[str, object]) -> str:
    """Return a one-line parameter summary from a JSON schema, e.g. 'query (str, req), max_results (int)'."""
    props: dict[str, object] = schema.get("properties", {})  # type: ignore[assignment]
    required: list[str] = schema.get("required", [])  # type: ignore[assignment]
    if not props:
        return "no parameters"
    parts: list[str] = []
    for name, info in props.items():
        assert isinstance(info, dict)
        typ = info.get("type", "any")
        req = "req" if name in required else "opt"
        parts.append(f"{name} ({typ}, {req})")
    return ", ".join(parts)


class ToolSearchTool:
    """Search the available tools by keyword.

    Returns each match with its name, description, and full parameter list so
    the agent knows exactly how to call the tool without guessing.

    Pass the same list you give the agent (minus this tool itself)::

        tools = [CalculatorTool(), WebSearchTool(), WikipediaTool()]
        agent = ReActAgent("bot", runtime, model=llm,
                           tools=[*tools, ToolSearchTool(tools)])
    """

    name = "tool_search"
    description = (
        "Search available tools by keyword. "
        "Returns name, description, and parameters for each match. "
        "Use an empty string to list all tools."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Keyword to search (case-insensitive). Empty string lists all tools.",
            },
            "format": {
                "type": "string",
                "enum": ["text", "schema"],
                "description": (
                    "'text' (default) returns readable name + description + parameters. "
                    "'schema' returns full JSON schemas for programmatic use."
                ),
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    risk = ToolRisk.SAFE

    def __init__(self, tools: list[Tool]) -> None:
        self._tools: dict[str, Tool] = {t.name: t for t in tools}

    async def execute(
        self, *, query: str = "", format: str = "text", **_: object
    ) -> ToolExecutionResult:
        q = query.strip().lower()
        matches = [
            t
            for t in self._tools.values()
            if not q or q in t.name.lower() or q in t.description.lower()
        ]

        if not matches:
            msg = f"No tools found matching '{query}'." if q else "No tools registered."
            return ToolExecutionResult(content=[TextBlock(text=msg)])

        if format == "schema":
            schemas = [
                {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                }
                for t in matches
            ]
            text = json.dumps({"tools": schemas, "query": query}, indent=2)
        else:
            header = "Available tools:" if not q else f"Tools matching '{query}':"
            lines: list[str] = []
            for t in matches:
                params = _param_summary(t.input_schema)  # type: ignore[arg-type]
                lines.append(f"• **{t.name}**: {t.description}\n  Parameters: {params}")
            text = header + "\n\n" + "\n\n".join(lines)

        return ToolExecutionResult(content=[TextBlock(text=text)])
