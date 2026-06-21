"""WikipediaTool — search and fetch Wikipedia articles via the REST API."""

from __future__ import annotations

import httpx

from agent_substrate.kernel import TextBlock
from agent_substrate.kernel.tools import ToolExecutionResult

_BASE = "https://en.wikipedia.org/api/rest_v1"
_MAX_CHARS = 6000


class WikipediaTool:
    """Look up a topic on Wikipedia and return a summary or full extract.

    Uses the Wikipedia REST API — no API key required.

    Example::

        from agent_substrate.capabilities.tools import WikipediaTool
        agent = ReActAgent("bot", runtime, model=llm, tools=[WikipediaTool()])
    """

    name = "wikipedia"
    description = (
        "Search Wikipedia for a topic and return a summary or article extract. "
        "Great for factual lookups, definitions, and background knowledge."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Topic or article title to look up on Wikipedia.",
            },
            "full_article": {
                "type": "boolean",
                "description": "If true, return the full article extract instead of just the summary. Defaults to false.",
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum number of characters to return (only applies to full article extracts). Defaults to 6000.",
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
        full_article: bool = False,
        max_chars: int | None = None,
        **_: object,
    ) -> ToolExecutionResult:
        try:
            async with httpx.AsyncClient(
                timeout=10.0,
                headers={"User-Agent": "ravi-agent/1.0"},
            ) as client:
                # Search for the best matching article title
                search_resp = await client.get(
                    "https://en.wikipedia.org/w/api.php",
                    params={
                        "action": "query",
                        "list": "search",
                        "srsearch": query,
                        "srlimit": 1,
                        "format": "json",
                    },
                )
                search_resp.raise_for_status()
                search_data = search_resp.json()
                hits = search_data.get("query", {}).get("search", [])
                if not hits:
                    return ToolExecutionResult(
                        content=[
                            TextBlock(text=f"No Wikipedia article found for: {query}")
                        ],
                    )

                title = hits[0]["title"]

                if full_article:
                    # Fetch full plain-text extract
                    article_resp = await client.get(
                        "https://en.wikipedia.org/w/api.php",
                        params={
                            "action": "query",
                            "titles": title,
                            "prop": "extracts",
                            "explaintext": True,
                            "format": "json",
                        },
                    )
                    article_resp.raise_for_status()
                    pages = article_resp.json().get("query", {}).get("pages", {})
                    page = next(iter(pages.values()))
                    text = page.get("extract", "").strip()
                    limit = max_chars if max_chars is not None else _MAX_CHARS
                    if len(text) > limit:
                        text = text[:limit] + "\n\n[truncated — article continues]"
                    output = f"# {title}\n\n{text}"
                else:
                    # Summary endpoint — short and clean
                    summary_resp = await client.get(
                        f"{_BASE}/page/summary/{httpx.URL(title).path}",
                    )
                    if summary_resp.status_code == 404:
                        # Fall back to extract for titles with special chars
                        summary_resp = await client.get(
                            "https://en.wikipedia.org/w/api.php",
                            params={
                                "action": "query",
                                "titles": title,
                                "prop": "extracts",
                                "exintro": True,
                                "explaintext": True,
                                "format": "json",
                            },
                        )
                        summary_resp.raise_for_status()
                        pages = summary_resp.json().get("query", {}).get("pages", {})
                        page = next(iter(pages.values()))
                        extract = page.get("extract", "").strip()
                        output = f"# {title}\n\n{extract}"
                    else:
                        summary_resp.raise_for_status()
                        data = summary_resp.json()
                        summary = data.get("extract", "").strip()
                        url = (
                            data.get("content_urls", {})
                            .get("desktop", {})
                            .get("page", "")
                        )
                        output = f"# {title}\n\n{summary}"
                        if url:
                            output += f"\n\nSource: {url}"

            return ToolExecutionResult(content=[TextBlock(text=output)])

        except httpx.TimeoutException:
            return ToolExecutionResult(
                content=[TextBlock(text="Wikipedia request timed out.")],
                is_error=True,
            )
        except Exception as exc:
            return ToolExecutionResult(
                content=[TextBlock(text=f"Wikipedia lookup failed: {exc}")],
                is_error=True,
            )
