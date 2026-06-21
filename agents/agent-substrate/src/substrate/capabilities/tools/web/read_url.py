"""ReadUrlTool — fetch a URL and return query-relevant content."""

from __future__ import annotations

from substrate.kernel import TextBlock
from substrate.kernel.tools import ToolExecutionResult

_MAX_CHARS = 6_000


def _extract_relevant(text: str, query: str | None, max_chars: int) -> str:
    """Return the most query-relevant paragraphs from *text* up to *max_chars*.

    Paragraphs are scored by keyword overlap with *query* and returned in their
    original reading order.  When no query is given, falls back to the first
    *max_chars* characters (same behaviour as before).
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if len(p.strip()) > 50]
    if not query or not paragraphs:
        return text[:max_chars]

    query_words = set(query.lower().split())

    def _score(p: str) -> int:
        return len(query_words & set(p.lower().split()))

    # Pick highest-scoring paragraphs greedily, then restore reading order.
    order = sorted(
        range(len(paragraphs)), key=lambda i: _score(paragraphs[i]), reverse=True
    )
    selected: list[int] = []
    total = 0
    for i in order:
        seg_len = len(paragraphs[i])
        if total + seg_len > max_chars:
            break
        selected.append(i)
        total += seg_len

    if not selected:
        # Nothing fit — return the highest-scoring paragraph truncated.
        return paragraphs[order[0]][:max_chars]

    selected.sort()
    return "\n\n".join(paragraphs[i] for i in selected)


class ReadUrlTool:
    """Fetch a web page and return query-relevant content.

    Provider priority: Tavily Extract → Exa /contents → crawl4ai fallback.

    Tavily and Exa return pre-extracted text, avoiding a full-page crawl.
    crawl4ai is the free fallback with keyword-scoring for relevance.
    Pass ``query`` to improve relevance on all paths.
    """

    name = "read_url"
    description = (
        "Fetch a web page or article and return its most relevant content. "
        "Pass the 'query' parameter (what you're looking for) to get a focused extract "
        "instead of the full page — this saves tokens and avoids missing buried information. "
        "Works on news articles, documentation, Wikipedia, and blog posts."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Full URL to fetch, e.g. 'https://example.com/article'.",
            },
            "query": {
                "type": "string",
                "description": (
                    "What you are looking for on this page. "
                    "Providing this returns only the most relevant sections, "
                    "dramatically reducing token usage."
                ),
            },
            "offset": {
                "type": "integer",
                "description": (
                    "Character offset to start reading from (default 0). "
                    "If the result says '[truncated]', call again with offset= "
                    "the previous chunk size to read the next page. "
                    "Only used when no query is provided."
                ),
                "minimum": 0,
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum number of characters to return. Defaults to 6000.",
                "minimum": 500,
                "maximum": 50000,
            },
        },
        "required": ["url"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        *,
        tavily_api_key: str | None = None,
        exa_api_key: str | None = None,
        max_chars: int = _MAX_CHARS,
    ) -> None:
        self._tavily_key = tavily_api_key or None
        self._exa_key = exa_api_key or None
        self._default_max_chars = max_chars

    async def execute(
        self,
        *,
        url: str,
        query: str | None = None,
        offset: int = 0,
        max_chars: int | None = None,
        **_: object,
    ) -> ToolExecutionResult:
        limit = int(max_chars or self._default_max_chars)

        if self._tavily_key:
            return await self._fetch_tavily(url, query, limit)
        if self._exa_key:
            return await self._fetch_exa(url, query, limit)
        return await self._fetch_crawl4ai(url, query, offset, limit)

    # ── Tavily Extract ────────────────────────────────────────────────────────

    async def _fetch_tavily(
        self, url: str, query: str | None, limit: int
    ) -> ToolExecutionResult:
        try:
            import asyncio
            from tavily import AsyncTavilyClient

            client = AsyncTavilyClient(api_key=self._tavily_key)
            response = await asyncio.wait_for(
                client.extract(urls=[url]),
                timeout=15,
            )

            results = response.get("results", [])
            if not results:
                failed = response.get("failed_results", [])
                reason = (
                    failed[0].get("error", "no content") if failed else "no content"
                )
                return ToolExecutionResult(
                    content=[TextBlock(text=f"Failed to extract {url}: {reason}")],
                    is_error=True,
                )

            text = (results[0].get("raw_content") or "").strip()
            if not text:
                return ToolExecutionResult(
                    content=[TextBlock(text=f"No content extracted from {url}")],
                    is_error=True,
                )

            if query:
                text = _extract_relevant(text, query, limit)
            elif len(text) > limit:
                text = (
                    text[:limit]
                    + "\n\n[truncated — call again with a more specific query]"
                )

            return ToolExecutionResult(content=[TextBlock(text=text)])

        except Exception as exc:
            return ToolExecutionResult(
                content=[TextBlock(text=f"Failed to fetch {url}: {exc}")],
                is_error=True,
            )

    # ── Exa /contents ────────────────────────────────────────────────────────

    async def _fetch_exa(
        self, url: str, query: str | None, limit: int
    ) -> ToolExecutionResult:
        try:
            import asyncio
            from exa_py import Exa

            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: Exa(api_key=self._exa_key).get_contents([url], highlights=True),
            )

            results = response.results if response.results else []
            if not results:
                return ToolExecutionResult(
                    content=[TextBlock(text=f"No content extracted from {url}")],
                    is_error=True,
                )

            r = results[0]
            highlights = getattr(r, "highlights", None) or []
            if highlights:
                text = "\n\n".join(h.strip() for h in highlights if h.strip())
            else:
                # Fall back to text field if highlights unavailable.
                text = (getattr(r, "text", None) or "").strip()

            if not text:
                return ToolExecutionResult(
                    content=[TextBlock(text=f"No content extracted from {url}")],
                    is_error=True,
                )

            if len(text) > limit:
                text = (
                    text[:limit]
                    + "\n\n[truncated — call again with a more specific query]"
                )

            return ToolExecutionResult(content=[TextBlock(text=text)])

        except Exception as exc:
            return ToolExecutionResult(
                content=[TextBlock(text=f"Failed to fetch {url}: {exc}")],
                is_error=True,
            )

    # ── crawl4ai + keyword scoring (fallback) ─────────────────────────────────

    async def _fetch_crawl4ai(
        self, url: str, query: str | None, offset: int, limit: int
    ) -> ToolExecutionResult:
        try:
            from crawl4ai import AsyncWebCrawler, HTTPCrawlerConfig
            from crawl4ai.async_crawler_strategy import AsyncHTTPCrawlerStrategy

            config = HTTPCrawlerConfig(
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
                    "Sec-Ch-Ua-Mobile": "?0",
                    "Sec-Ch-Ua-Platform": '"Windows"',
                    "Upgrade-Insecure-Requests": "1",
                }
            )
            async with AsyncWebCrawler(
                crawler_strategy=AsyncHTTPCrawlerStrategy(browser_config=config),
                verbose=False,
            ) as crawler:
                result = await crawler.arun(url)

            if not result.success:
                return ToolExecutionResult(
                    content=[
                        TextBlock(text=f"Failed to fetch {url}: {result.error_message}")
                    ],
                    is_error=True,
                )

            text = (result.markdown or "").strip()
            if not text:
                return ToolExecutionResult(
                    content=[TextBlock(text=f"No content extracted from {url}")],
                    is_error=True,
                )

            if query:
                chunk = _extract_relevant(text, query, limit)
            else:
                total = len(text)
                chunk = text[offset : offset + limit]
                remaining = total - offset - len(chunk)
                if remaining > 0:
                    chunk += f"\n\n[truncated — {remaining} chars remaining, call again with offset={offset + len(chunk)}]"

            return ToolExecutionResult(content=[TextBlock(text=chunk)])

        except Exception as exc:
            return ToolExecutionResult(
                content=[TextBlock(text=f"Failed to fetch {url}: {exc}")],
                is_error=True,
            )
