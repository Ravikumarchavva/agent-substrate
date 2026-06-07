"""ReadUrlTool — fetch a URL and return clean Markdown via crawl4ai."""

from __future__ import annotations

from ravi.kernel import TextBlock
from ravi.kernel.tools import ToolExecutionResult

_MAX_CHARS = 12_000


class ReadUrlTool:
    """Fetch a web page and return its content as clean Markdown.

    Uses `crawl4ai <https://crawl4ai.com>`_ with its HTTP crawler strategy —
    no browser binary required.  Returns well-structured Markdown with links,
    headings, and tables preserved.

    Example::

        from ravi.capabilities.tools import ReadUrlTool
        agent = ReActAgent("bot", runtime, model=llm, tools=[ReadUrlTool()])
    """

    name = "read_url"
    description = (
        "Fetch a web page or article and return its content as clean Markdown. "
        "Works on news articles, documentation, Wikipedia, and blog posts."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Full URL to fetch, e.g. 'https://example.com/article'.",
            },
            "offset": {
                "type": "integer",
                "description": (
                    "Character offset to start reading from (default 0). "
                    "If the result says '[truncated]', call again with offset= the previous chunk size to read the next page."
                ),
                "minimum": 0,
            },
        },
        "required": ["url"],
        "additionalProperties": False,
    }

    async def execute(self, *, url: str, offset: int = 0, **_: object) -> ToolExecutionResult:
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
                    content=[TextBlock(text=f"Failed to fetch {url}: {result.error_message}")],
                    is_error=True,
                )

            text = (result.markdown or "").strip()
            if not text:
                return ToolExecutionResult(
                    content=[TextBlock(text=f"No content extracted from {url}")],
                    is_error=True,
                )

            total = len(text)
            chunk = text[offset: offset + _MAX_CHARS]
            remaining = total - offset - len(chunk)
            if remaining > 0:
                chunk += f"\n\n[truncated — {remaining} chars remaining, call again with offset={offset + len(chunk)}]"

            return ToolExecutionResult(content=[TextBlock(text=chunk)])

        except Exception as exc:
            return ToolExecutionResult(
                content=[TextBlock(text=f"Failed to fetch {url}: {exc}")],
                is_error=True,
            )
