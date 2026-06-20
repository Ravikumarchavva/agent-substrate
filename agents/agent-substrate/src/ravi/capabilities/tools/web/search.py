"""WebSearchTool — multi-provider web search (Exa → Tavily → DuckDuckGo)."""

from __future__ import annotations

import asyncio
from functools import partial

from ravi.kernel import TextBlock
from ravi.kernel.tools import ToolExecutionResult


class WebSearchTool:
    """Search the web with automatic provider selection.

    Provider priority (first key found wins):
      1. Exa      — neural search + query-relevant highlights (best quality, ~1s)
      2. Tavily   — pre-extracted page content (good quality, ~1-2s)
      3. DuckDuckGo — snippets only, no API key required (fallback)

    Pass API keys via the constructor (wired from settings in serving_factory).
    If no keys are supplied, DuckDuckGo is used automatically.
    """

    name = "web_search"
    description = (
        "Search the web for current information. "
        "Returns titles, URLs, and relevant excerpts from the top results."
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
                "description": "Maximum number of results to return (1–10). Defaults to 3.",
                "minimum": 1,
                "maximum": 10,
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum characters in the combined result. Defaults to 5000.",
                "minimum": 500,
                "maximum": 50000,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        *,
        exa_api_key: str | None = None,
        tavily_api_key: str | None = None,
        max_results: int = 3,
        max_chars: int = 5000,
    ) -> None:
        self._exa_key = exa_api_key or None
        self._tavily_key = tavily_api_key or None
        self._default_max_results = max_results
        self._default_max_chars = max_chars

    async def execute(
        self,
        *,
        query: str,
        max_results: int | None = None,
        max_chars: int | None = None,
        **_: object,
    ) -> ToolExecutionResult:
        n = max(1, min(10, int(max_results or self._default_max_results)))
        limit = int(max_chars or self._default_max_chars)

        if self._exa_key:
            return await self._search_exa(query, n, limit)
        if self._tavily_key:
            return await self._search_tavily(query, n, limit)
        return await self._search_ddgs(query, n, limit)

    # ── Exa ──────────────────────────────────────────────────────────────────

    async def _search_exa(self, query: str, n: int, limit: int) -> ToolExecutionResult:
        try:
            from exa_py import Exa

            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: Exa(api_key=self._exa_key).search(
                    query,
                    type="auto",
                    num_results=n,
                    contents={"highlights": True},
                ),
            )

            if not response.results:
                return ToolExecutionResult(
                    content=[TextBlock(text=f"No results found for: {query}")]
                )

            parts: list[str] = [f"Search results for '{query}' [via Exa]:\n"]
            for r in response.results:
                title = (r.title or "").strip()
                url = (r.url or "").strip()
                highlights = getattr(r, "highlights", None) or []
                excerpt = "\n  • ".join(h.strip() for h in highlights if h.strip())
                if excerpt:
                    parts.append(f"{title} · {url}\n  • {excerpt}")
                else:
                    parts.append(f"{title} · {url}")

            return self._build_result("\n\n".join(parts), limit)

        except Exception as exc:
            return ToolExecutionResult(
                content=[TextBlock(text=f"Exa search failed: {exc}")],
                is_error=True,
            )

    # ── Tavily ────────────────────────────────────────────────────────────────

    async def _search_tavily(
        self, query: str, n: int, limit: int
    ) -> ToolExecutionResult:
        try:
            from tavily import TavilyClient

            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: TavilyClient(api_key=self._tavily_key).search(
                    query, max_results=n
                ),
            )

            results = response.get("results", [])
            if not results:
                return ToolExecutionResult(
                    content=[TextBlock(text=f"No results found for: {query}")]
                )

            parts: list[str] = [f"Search results for '{query}' [via Tavily]:\n"]
            for r in results:
                title = (r.get("title") or "").strip()
                url = (r.get("url") or "").strip()
                content = (r.get("content") or "").strip()
                parts.append(f"{title} · {url}\n  {content}")

            return self._build_result("\n\n".join(parts), limit)

        except Exception as exc:
            return ToolExecutionResult(
                content=[TextBlock(text=f"Tavily search failed: {exc}")],
                is_error=True,
            )

    # ── DuckDuckGo (fallback) ─────────────────────────────────────────────────

    async def _search_ddgs(self, query: str, n: int, limit: int) -> ToolExecutionResult:
        try:
            from ddgs import DDGS

            loop = asyncio.get_event_loop()
            hits = await loop.run_in_executor(
                None, partial(DDGS().text, query, max_results=n)
            )

            if not hits:
                return ToolExecutionResult(
                    content=[TextBlock(text=f"No results found for: {query}")]
                )

            parts: list[str] = [f"Search results for '{query}':\n"]
            for i, r in enumerate(hits, 1):
                title = r.get("title", "").strip()
                body = r.get("body", "").strip()
                url = r.get("href", "").strip()
                parts.append(f"{i}. {title} · {url}\n   {body}")

            return self._build_result("\n\n".join(parts), limit)

        except Exception as exc:
            return ToolExecutionResult(
                content=[TextBlock(text=f"Search failed: {exc}")],
                is_error=True,
            )

    # ── Shared ────────────────────────────────────────────────────────────────

    @staticmethod
    def _build_result(text: str, limit: int) -> ToolExecutionResult:
        if len(text) > limit:
            text = text[:limit] + "\n\n[truncated — use read_url for full page content]"
        return ToolExecutionResult(content=[TextBlock(text=text)])
