"""WebSearchTool — search the web via DuckDuckGo (no API key required)."""

from __future__ import annotations

import asyncio
from functools import partial

from ravi.kernel import TextBlock
from ravi.kernel.tools import ToolExecutionResult


class WebSearchTool:
    """Search the web using DuckDuckGo — no API key needed.

    Returns titles, URLs, and snippets for the top results.
    Uses the ``ddgs`` library under the hood.

    Example::

        from ravi.capabilities.tools import WebSearchTool
        agent = ReActAgent("bot", runtime, model=llm, tools=[WebSearchTool()])
    """

    name = "web_search"
    description = (
        "Search the web for current information. "
        "Returns titles, URLs, and snippets for the top results."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return (1–10). Defaults to 5.",
                "minimum": 1,
                "maximum": 10,
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum number of characters of the combined results string. Defaults to 10000.",
                "minimum": 1000,
                "maximum": 50000,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    async def execute(
        self,
        *,
        query: str,
        max_results: int = 5,
        max_chars: int | None = None,
        **_: object,
    ) -> ToolExecutionResult:
        from ddgs import DDGS

        max_results = max(1, min(10, int(max_results)))
        try:
            loop = asyncio.get_event_loop()
            hits = await loop.run_in_executor(
                None, partial(DDGS().text, query, max_results=max_results)
            )

            if not hits:
                return ToolExecutionResult(
                    content=[TextBlock(text=f"No results found for: {query}")]
                )

            lines: list[str] = [f"Search results for '{query}':\n"]
            for i, r in enumerate(hits, 1):
                title = r.get("title", "").strip()
                body = r.get("body", "").strip()
                url = r.get("href", "").strip()
                lines.append(f"{i}. **{title}**\n   {body}\n   {url}")

            limit = max_chars if max_chars is not None else 10000
            result_str = "\n\n".join(lines)
            if len(result_str) > limit:
                result_str = result_str[:limit] + "\n\n[truncated due to max_chars limit]"

            return ToolExecutionResult(content=[TextBlock(text=result_str)])

        except Exception as exc:
            return ToolExecutionResult(
                content=[TextBlock(text=f"Search failed: {exc}")],
                is_error=True,
            )
