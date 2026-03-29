"""HttpRequestTool — make HTTP requests with configurable URL allowlist.

Allows the agent to call external REST APIs.  A domain allowlist is
enforced to prevent SSRF and limit network exposure.
"""

from __future__ import annotations

from typing import Dict, List, Optional
from urllib.parse import urlparse

from raavan.core.tools.base_tool import BaseTool, ToolResult, ToolRisk


_DEFAULT_ALLOWED_DOMAINS: List[str] = [
    "api.github.com",
    "httpbin.org",
    "jsonplaceholder.typicode.com",
]


class HttpRequestTool(BaseTool):
    """Make HTTP GET/POST/PUT/DELETE requests with domain allowlisting."""

    def __init__(
        self,
        allowed_domains: Optional[List[str]] = None,
    ) -> None:
        self._allowed_domains = set(allowed_domains or _DEFAULT_ALLOWED_DOMAINS)
        super().__init__(
            name="http_request",
            description=(
                "Make an HTTP request to a URL. Supports GET, POST, PUT, and DELETE methods. "
                "Only pre-approved domains are reachable for security."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Full URL to request (must be an allowed domain)",
                    },
                    "method": {
                        "type": "string",
                        "enum": ["GET", "POST", "PUT", "DELETE"],
                        "description": "HTTP method (default: GET)",
                    },
                    "headers": {
                        "type": "object",
                        "description": "Request headers as key-value pairs",
                    },
                    "body": {
                        "type": "string",
                        "description": "Request body (for POST/PUT)",
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            risk=ToolRisk.SENSITIVE,
            category="development/execution",
            tags=["api", "http", "rest", "request", "fetch", "endpoint", "curl"],
            aliases=["api_request", "fetch_url"],
        )

    def _is_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        return hostname in self._allowed_domains

    async def execute(  # type: ignore[override]
        self,
        *,
        url: str,
        method: str = "GET",
        headers: Optional[Dict[str, str]] = None,
        body: str = "",
    ) -> ToolResult:
        if not self._is_allowed(url):
            parsed = urlparse(url)
            return ToolResult(
                content=[
                    {
                        "type": "text",
                        "text": (
                            f"Domain '{parsed.hostname}' is not in the allowed list. "
                            f"Allowed: {', '.join(sorted(self._allowed_domains))}"
                        ),
                    }
                ],
                is_error=True,
            )

        import httpx

        method = method.upper()
        req_headers = dict(headers) if headers else {}
        req_headers.setdefault("User-Agent", "agent-framework/1.0")

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=req_headers,
                    content=body if body else None,
                )
        except httpx.HTTPError as exc:
            return ToolResult(
                content=[{"type": "text", "text": f"HTTP error: {exc}"}],
                is_error=True,
            )

        # Truncate very large responses
        body_text = response.text[:8000]
        if len(response.text) > 8000:
            body_text += f"\n... (truncated, total {len(response.text)} chars)"

        result_text = (
            f"HTTP {response.status_code} {method} {url}\n"
            f"Content-Type: {response.headers.get('content-type', 'unknown')}\n\n"
            f"{body_text}"
        )
        return ToolResult(
            content=[{"type": "text", "text": result_text}],
            app_data={
                "status_code": response.status_code,
                "url": url,
                "method": method,
            },
        )
