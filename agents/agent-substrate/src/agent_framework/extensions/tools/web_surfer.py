"""Web Surfer Tool - Agentic web browsing with Playwright."""
import asyncio
import base64
from typing import Any, Optional, Literal
from datetime import datetime

try:
    from playwright.async_api import async_playwright, Browser, Page, TimeoutError as PlaywrightTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

from .base_tool import BaseTool, Tool, ToolResult


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
    
    def __init__(self, headless: bool = True, browser_type: Literal["chromium", "firefox", "webkit"] = "chromium"):
        """Initialize web surfer tool.
        
        Args:
            headless: Run browser in headless mode (default: True)
            browser_type: Browser engine to use (chromium, firefox, or webkit)
        """
        if not PLAYWRIGHT_AVAILABLE:
            raise ImportError(
                "Playwright is required for WebSurferTool. "
                "Install it with: pip install playwright && playwright install"
            )
        
        self.headless = headless
        self.browser_type = browser_type
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None
        
        self.tool_schema = Tool(
            name="web_surfer",
            description=(
                "Advanced web browsing tool for agents. Supports navigation, content extraction, "
                "screenshots, element interaction, and form filling. Maintains browser session "
                "across multiple actions."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "navigate", "extract_text", "extract_markdown", "get_html",
                            "screenshot", "click", "fill", "scroll", "execute_js",
                            "get_metadata", "go_back", "go_forward", "close"
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
                        )
                    },
                    "url": {
                        "type": "string",
                        "description": "URL to navigate to (for 'navigate' action)"
                    },
                    "selector": {
                        "type": "string",
                        "description": "CSS selector for element (for 'click', 'fill' actions)"
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to enter (for 'fill' action)"
                    },
                    "javascript": {
                        "type": "string",
                        "description": "JavaScript code to execute (for 'execute_js' action)"
                    },
                    "scroll_direction": {
                        "type": "string",
                        "enum": ["up", "down", "top", "bottom"],
                        "description": "Direction to scroll (for 'scroll' action)"
                    },
                    "full_page": {
                        "type": "boolean",
                        "description": "Take full page screenshot (default: true for 'screenshot' action)"
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Timeout in milliseconds (default: 30000)"
                    }
                },
                "required": ["action"]
            }
        )
    
    @property
    def name(self) -> str:
        return self.tool_schema.name
    
    @property
    def description(self) -> str:
        return self.tool_schema.description
    
    @property
    def input_schema(self) -> dict:
        return self.tool_schema.inputSchema
    
    async def _ensure_browser(self) -> None:
        """Ensure browser is initialized and ready."""
        if self._browser is None:
            self._playwright = await async_playwright().start()
            
            if self.browser_type == "firefox":
                self._browser = await self._playwright.firefox.launch(headless=self.headless)
            elif self.browser_type == "webkit":
                self._browser = await self._playwright.webkit.launch(headless=self.headless)
            else:
                self._browser = await self._playwright.chromium.launch(headless=self.headless)
        
        if self._page is None:
            self._page = await self._browser.new_page()
            # Set reasonable defaults
            await self._page.set_viewport_size({"width": 1280, "height": 720})
    
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
    
    async def execute(
        self,
        action: str,
        url: Optional[str] = None,
        selector: Optional[str] = None,
        text: Optional[str] = None,
        javascript: Optional[str] = None,
        scroll_direction: Optional[str] = None,
        full_page: bool = True,
        timeout: int = 30000
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
                    content=[{
                        "type": "text",
                        "text": "Browser session closed successfully"
                    }],
                    isError=False
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
                    raise ValueError("JavaScript code is required for execute_js action")
                result = await self._execute_js(javascript)
            
            elif action == "get_metadata":
                result = await self._get_metadata()
            
            elif action == "go_back":
                await self._page.go_back(timeout=timeout)
                result = {"status": "success", "action": "go_back", "url": self._page.url}
            
            elif action == "go_forward":
                await self._page.go_forward(timeout=timeout)
                result = {"status": "success", "action": "go_forward", "url": self._page.url}
            
            else:
                raise ValueError(f"Unknown action: {action}")
            
            # Format result
            if isinstance(result, dict) and "screenshot" in result:
                # Handle screenshot with image content
                return ToolResult(
                    content=[
                        {
                            "type": "text",
                            "text": f"Screenshot captured: {result['url']}"
                        },
                        {
                            "type": "image",
                            "data": result["screenshot"],
                            "mimeType": "image/png"
                        }
                    ],
                    isError=False
                )
            else:
                # Handle text results
                import json
                return ToolResult(
                    content=[{
                        "type": "text",
                        "text": json.dumps(result, indent=2)
                    }],
                    isError=False
                )
        
        except Exception as e:
            return ToolResult(
                content=[{
                    "type": "text",
                    "text": f"Error executing {action}: {str(e)}"
                }],
                isError=True
            )
    
    async def _navigate(self, url: str, timeout: int) -> dict[str, Any]:
        """Navigate to URL."""
        response = await self._page.goto(url, timeout=timeout, wait_until="domcontentloaded")
        return {
            "status": "success",
            "action": "navigate",
            "url": self._page.url,
            "title": await self._page.title(),
            "status_code": response.status if response else None
        }
    
    async def _extract_text(self) -> dict[str, Any]:
        """Extract visible text from page."""
        text = await self._page.evaluate("""
            () => {
                return document.body.innerText;
            }
        """)
        return {
            "status": "success",
            "action": "extract_text",
            "url": self._page.url,
            "text": text,
            "length": len(text)
        }
    
    async def _extract_markdown(self) -> dict[str, Any]:
        """Extract content as markdown (simplified)."""
        # Simple markdown conversion - can be enhanced
        markdown = await self._page.evaluate("""
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
            "url": self._page.url,
            "markdown": markdown
        }
    
    async def _get_html(self) -> dict[str, Any]:
        """Get page HTML source."""
        html = await self._page.content()
        return {
            "status": "success",
            "action": "get_html",
            "url": self._page.url,
            "html": html,
            "length": len(html)
        }
    
    async def _screenshot(self, full_page: bool) -> dict[str, Any]:
        """Take page screenshot."""
        screenshot_bytes = await self._page.screenshot(full_page=full_page, type="png")
        screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")
        return {
            "status": "success",
            "action": "screenshot",
            "url": self._page.url,
            "screenshot": screenshot_base64,
            "full_page": full_page
        }
    
    async def _click(self, selector: str, timeout: int) -> dict[str, Any]:
        """Click element by selector."""
        await self._page.click(selector, timeout=timeout)
        return {
            "status": "success",
            "action": "click",
            "selector": selector,
            "url": self._page.url
        }
    
    async def _fill(self, selector: str, text: str, timeout: int) -> dict[str, Any]:
        """Fill input field."""
        await self._page.fill(selector, text, timeout=timeout)
        return {
            "status": "success",
            "action": "fill",
            "selector": selector,
            "url": self._page.url
        }
    
    async def _scroll(self, direction: str) -> dict[str, Any]:
        """Scroll page in specified direction."""
        if direction == "down":
            await self._page.evaluate("window.scrollBy(0, window.innerHeight)")
        elif direction == "up":
            await self._page.evaluate("window.scrollBy(0, -window.innerHeight)")
        elif direction == "top":
            await self._page.evaluate("window.scrollTo(0, 0)")
        elif direction == "bottom":
            await self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        
        return {
            "status": "success",
            "action": "scroll",
            "direction": direction,
            "url": self._page.url
        }
    
    async def _execute_js(self, javascript: str) -> dict[str, Any]:
        """Execute JavaScript code on page."""
        result = await self._page.evaluate(javascript)
        return {
            "status": "success",
            "action": "execute_js",
            "result": result,
            "url": self._page.url
        }
    
    async def _get_metadata(self) -> dict[str, Any]:
        """Get page metadata."""
        metadata = await self._page.evaluate("""
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
        metadata.update({
            "status": "success",
            "action": "get_metadata",
            "url": self._page.url,
            "timestamp": datetime.utcnow().isoformat()
        })
        return metadata
    
    async def __aenter__(self):
        """Async context manager entry."""
        await self._ensure_browser()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self._close_browser()
