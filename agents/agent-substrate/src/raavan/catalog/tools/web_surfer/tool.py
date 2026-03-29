"""Web Surfer Tool - Agentic web browsing with Playwright."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Optional

from raavan.core.tools.base_tool import BaseTool, ToolResult, ToolRisk

if TYPE_CHECKING:
    from playwright.async_api import Browser, Page, Playwright
else:
    Browser = Any
    Page = Any
    Playwright = Any

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

PLAYWRIGHT_AVAILABLE = async_playwright is not None


class WebSurferTool(BaseTool):
    """Agentic web surfing tool with browser automation capabilities.

    Provides comprehensive web browsing actions:
    - Navigate to URLs
    - Extract page content (text, markdown, HTML)
    - Take screenshots
    - Click elements
    - Fill forms
    - Scroll pages
    - Execute JavaScript
    - Get page metadata

    Uses Playwright for reliable browser automation.
    Maintains browser session for multi-step workflows.
    """

    risk: ClassVar[ToolRisk] = ToolRisk.SENSITIVE  # external network reads

    def __init__(
        self,
        headless: bool = True,
        browser_type: Literal["chromium", "firefox", "webkit"] = "chromium",
    ):
        """Initialize web surfer tool.

        Args:
            headless: Run browser in headless mode (default: True)
            browser_type: Browser engine to use (chromium, firefox, or webkit)
        """
        if not PLAYWRIGHT_AVAILABLE:
            raise ImportError(
                "Playwright is required for WebSurferTool. "
                "Install it with: uv sync --group browser && uv run playwright install"
            )

        self.headless = headless
        self.browser_type = browser_type
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None

        super().__init__(
            name="web_surfer",
            description=(
                "Advanced web browsing tool for agents. Supports navigation, content extraction, "
                "screenshots, element interaction, and form filling. Maintains browser session "
                "across multiple actions."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "navigate",
                            "extract_text",
                            "extract_markdown",
                            "get_html",
                            "screenshot",
                            "click",
                            "fill",
                            "scroll",
                            "execute_js",
                            "get_metadata",
                            "go_back",
                            "go_forward",
                            "close",
                        ],
                        "description": (
                            "Action to perform:\n"
                            "- navigate: Go to URL\n"
                            "- extract_text: Get visible text content\n"
                            "- extract_markdown: Get content as markdown\n"
                            "- get_html: Get page HTML source\n"
                            "- screenshot: Take page screenshot\n"
                            "- click: Click element by selector\n"
                            "- fill: Fill input field\n"
                            "- scroll: Scroll page\n"
                            "- execute_js: Run JavaScript code\n"
                            "- get_metadata: Get page title, URL, metadata\n"
                            "- go_back: Navigate back\n"
                            "- go_forward: Navigate forward\n"
                            "- close: Close browser session"
                        ),
                    },
                    "url": {
                        "type": "string",
                        "description": "URL to navigate to (for 'navigate' action)",
                    },
                    "selector": {
                        "type": "string",
                        "description": "CSS selector for element (for 'click', 'fill' actions)",
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to enter (for 'fill' action)",
                    },
                    "javascript": {
                        "type": "string",
                        "description": "JavaScript code to execute (for 'execute_js' action)",
                    },
                    "scroll_direction": {
                        "type": "string",
                        "enum": ["up", "down", "top", "bottom"],
                        "description": "Direction to scroll (for 'scroll' action)",
                    },
                    "full_page": {
                        "type": "boolean",
                        "description": "Take full page screenshot (default: true for 'screenshot' action)",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Timeout in milliseconds (default: 30000)",
                    },
                },
                "required": ["action"],
            },
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": False,
                "openWorldHint": True,
                "title": "Web Surfer",
            },
            risk=self.risk,
        )

    async def _ensure_browser(self) -> None:
        """Ensure browser is initialized and ready."""
        if self._browser is None:
            playwright_factory = async_playwright
            if playwright_factory is None:
                raise ImportError(
                    "Playwright is required for WebSurferTool. "
                    "Install it with: uv sync --group browser && uv run playwright install"
                )

            self._playwright = await playwright_factory().start()

            if self.browser_type == "firefox":
                self._browser = await self._playwright.firefox.launch(
                    headless=self.headless
                )
            elif self.browser_type == "webkit":
                self._browser = await self._playwright.webkit.launch(
                    headless=self.headless
                )
            else:
                self._browser = await self._playwright.chromium.launch(
                    headless=self.headless
                )

        if self._page is None:
            browser = self._require_browser()
            self._page = await browser.new_page()
            # Set reasonable defaults
            page = self._require_page()
            await page.set_viewport_size({"width": 1280, "height": 720})

    def _require_browser(self) -> Browser:
        if self._browser is None:
            raise RuntimeError("Browser is not initialized")
        return self._browser

    def _require_page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Page is not initialized")
        return self._page

    async def _close_browser(self) -> None:
        """Close browser and cleanup resources."""
        if self._page:
            await self._page.close()
            self._page = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def execute(  # type: ignore[override]
        self,
        *,
        action: str,
        url: Optional[str] = None,
        selector: Optional[str] = None,
        text: Optional[str] = None,
        javascript: Optional[str] = None,
        scroll_direction: Optional[str] = None,
        full_page: bool = True,
        timeout: int = 30000,
    ) -> ToolResult:
        """Execute web surfing action.

        Args:
            action: Action to perform
            url: URL for navigation
            selector: CSS selector for element operations
            text: Text input for form filling
            javascript: JavaScript code to execute
            scroll_direction: Scroll direction
            full_page: Full page screenshot flag
            timeout: Operation timeout in milliseconds

        Returns:
            ToolResult with action outcome
        """

        try:
            # Close action doesn't need browser initialization
            if action == "close":
                await self._close_browser()
                return ToolResult(
                    content=[
                        {"type": "text", "text": "Browser session closed successfully"}
                    ],
                    is_error=False,
                )

            # Ensure browser is ready for all other actions
            await self._ensure_browser()

            # Execute action
            if action == "navigate":
                if not url:
                    raise ValueError("URL is required for navigate action")
                result = await self._navigate(url, timeout)

            elif action == "extract_text":
                result = await self._extract_text()

            elif action == "extract_markdown":
                result = await self._extract_markdown()

            elif action == "get_html":
                result = await self._get_html()

            elif action == "screenshot":
                result = await self._screenshot(full_page)

            elif action == "click":
                if not selector:
                    raise ValueError("Selector is required for click action")
                result = await self._click(selector, timeout)

            elif action == "fill":
                if not selector or not text:
                    raise ValueError("Selector and text are required for fill action")
                result = await self._fill(selector, text, timeout)

            elif action == "scroll":
                result = await self._scroll(scroll_direction or "down")

            elif action == "execute_js":
                if not javascript:
                    raise ValueError(
                        "JavaScript code is required for execute_js action"
                    )
                result = await self._execute_js(javascript)

            elif action == "get_metadata":
                result = await self._get_metadata()

            elif action == "go_back":
                page = self._require_page()
                await page.go_back(timeout=timeout)
                result = {
                    "status": "success",
                    "action": "go_back",
                    "url": page.url,
                }

            elif action == "go_forward":
                page = self._require_page()
                await page.go_forward(timeout=timeout)
                result = {
                    "status": "success",
                    "action": "go_forward",
                    "url": page.url,
                }

            else:
                raise ValueError(f"Unknown action: {action}")

            # Format result
            if isinstance(result, dict) and "screenshot" in result:
                # Handle screenshot with image content
                return ToolResult(
                    content=[
                        {
                            "type": "text",
                            "text": f"Screenshot captured: {result['url']}",
                        },
                        {
                            "type": "image",
                            "data": result["screenshot"],
                            "mimeType": "image/png",
                        },
                    ],
                    is_error=False,
                )
            else:
                # Handle text results
                import json

                return ToolResult(
                    content=[{"type": "text", "text": json.dumps(result, indent=2)}],
                    is_error=False,
                )

        except Exception as e:
            return ToolResult(
                content=[
                    {"type": "text", "text": f"Error executing {action}: {str(e)}"}
                ],
                is_error=True,
            )

    async def _navigate(self, url: str, timeout: int) -> dict[str, Any]:
        """Navigate to URL."""
        page = self._require_page()
        response = await page.goto(url, timeout=timeout, wait_until="domcontentloaded")
        return {
            "status": "success",
            "action": "navigate",
            "url": page.url,
            "title": await page.title(),
            "status_code": response.status if response else None,
        }

    async def _extract_text(self) -> dict[str, Any]:
        """Extract visible text from page."""
        page = self._require_page()
        text = await page.evaluate("""
            () => {
                return document.body.innerText;
            }
        """)
        return {
            "status": "success",
            "action": "extract_text",
            "url": page.url,
            "text": text,
            "length": len(text),
        }

    async def _extract_markdown(self) -> dict[str, Any]:
        """Extract content as markdown (simplified)."""
        # Simple markdown conversion - can be enhanced
        page = self._require_page()
        markdown = await page.evaluate("""
            () => {
                let md = '';
                
                // Title
                const title = document.querySelector('h1');
                if (title) md += '# ' + title.innerText + '\\n\\n';
                
                // Headings and paragraphs
                const elements = document.querySelectorAll('h2, h3, p, li, a');
                elements.forEach(el => {
                    if (el.tagName === 'H2') md += '## ' + el.innerText + '\\n\\n';
                    else if (el.tagName === 'H3') md += '### ' + el.innerText + '\\n\\n';
                    else if (el.tagName === 'P') md += el.innerText + '\\n\\n';
                    else if (el.tagName === 'LI') md += '- ' + el.innerText + '\\n';
                    else if (el.tagName === 'A') md += '[' + el.innerText + '](' + el.href + ') ';
                });
                
                return md;
            }
        """)
        return {
            "status": "success",
            "action": "extract_markdown",
            "url": page.url,
            "markdown": markdown,
        }

    async def _get_html(self) -> dict[str, Any]:
        """Get page HTML source."""
        page = self._require_page()
        html = await page.content()
        return {
            "status": "success",
            "action": "get_html",
            "url": page.url,
            "html": html,
            "length": len(html),
        }

    async def _screenshot(self, full_page: bool) -> dict[str, Any]:
        """Take page screenshot."""
        page = self._require_page()
        screenshot_bytes = await page.screenshot(full_page=full_page, type="png")
        screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")
        return {
            "status": "success",
            "action": "screenshot",
            "url": page.url,
            "screenshot": screenshot_base64,
            "full_page": full_page,
        }

    async def _click(self, selector: str, timeout: int) -> dict[str, Any]:
        """Click element by selector."""
        page = self._require_page()
        await page.click(selector, timeout=timeout)
        return {
            "status": "success",
            "action": "click",
            "selector": selector,
            "url": page.url,
        }

    async def _fill(self, selector: str, text: str, timeout: int) -> dict[str, Any]:
        """Fill input field."""
        page = self._require_page()
        await page.fill(selector, text, timeout=timeout)
        return {
            "status": "success",
            "action": "fill",
            "selector": selector,
            "url": page.url,
        }

    async def _scroll(self, direction: str) -> dict[str, Any]:
        """Scroll page in specified direction."""
        page = self._require_page()
        if direction == "down":
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
        elif direction == "up":
            await page.evaluate("window.scrollBy(0, -window.innerHeight)")
        elif direction == "top":
            await page.evaluate("window.scrollTo(0, 0)")
        elif direction == "bottom":
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

        return {
            "status": "success",
            "action": "scroll",
            "direction": direction,
            "url": page.url,
        }

    async def _execute_js(self, javascript: str) -> dict[str, Any]:
        """Execute JavaScript code on page."""
        page = self._require_page()
        result = await page.evaluate(javascript)
        return {
            "status": "success",
            "action": "execute_js",
            "result": result,
            "url": page.url,
        }

    async def _get_metadata(self) -> dict[str, Any]:
        """Get page metadata."""
        page = self._require_page()
        metadata = await page.evaluate("""
            () => {
                const meta = {};
                meta.title = document.title;
                meta.description = document.querySelector('meta[name="description"]')?.content || '';
                meta.keywords = document.querySelector('meta[name="keywords"]')?.content || '';
                meta.author = document.querySelector('meta[name="author"]')?.content || '';
                meta.ogTitle = document.querySelector('meta[property="og:title"]')?.content || '';
                meta.ogDescription = document.querySelector('meta[property="og:description"]')?.content || '';
                meta.ogImage = document.querySelector('meta[property="og:image"]')?.content || '';
                meta.links = Array.from(document.querySelectorAll('a')).slice(0, 50).map(a => ({
                    text: a.innerText.trim(),
                    href: a.href
                })).filter(l => l.text && l.href);
                return meta;
            }
        """)
        metadata.update(
            {
                "status": "success",
                "action": "get_metadata",
                "url": page.url,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        return metadata

    async def __aenter__(self):
        """Async context manager entry."""
        await self._ensure_browser()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self._close_browser()
