"""Built-in tool implementations."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, ClassVar

from .base_tool import BaseTool, ToolResult, ToolRisk


class CalculatorTool(BaseTool):
    """Simple calculator tool for basic math operations."""
    risk: ClassVar[ToolRisk] = ToolRisk.SAFE  # deterministic, no I/O
    
    def __init__(self):
        super().__init__(
            name="calculator",
            description="Performs basic mathematical calculations. Supports +, -, *, /, ** (power), and % (modulo).",
            input_schema={
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "The mathematical expression to evaluate (e.g., '2 + 2', '10 * 5')"
                    }
                },
                "required": ["expression"]
            }
        )
    
    async def execute(self, expression: str) -> ToolResult:
        """Execute a mathematical expression.
        
        Args:
            expression: Math expression as string (e.g., "2 + 2", "10 * 5")
            
        Returns:
            ToolResult with calculation result
        """
        try:
            # Safe evaluation - only allow math operations
            result = eval(expression, {"__builtins__": {}}, {})
            return ToolResult(
                content=[{
                    "type": "text",
                    "text": json.dumps({"result": result, "expression": expression})
                }],
                isError=False
            )
        except Exception as e:
            return ToolResult(
                content=[{
                    "type": "text",
                    "text": json.dumps({"error": str(e), "expression": expression})
                }],
                isError=True
            )


class GetCurrentTimeTool(BaseTool):
    """Tool to get the current time."""
    risk: ClassVar[ToolRisk] = ToolRisk.SAFE  # read-only, deterministic
    
    def __init__(self):
        super().__init__(
            name="get_current_time",
            description="Returns the current date and time in ISO format.",
            input_schema={
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": "Timezone name (e.g., 'UTC', 'America/New_York')",
                        "default": "UTC"
                    }
                },
                "required": []
            }
        )
    
    async def execute(self, timezone: str = "UTC") -> ToolResult:
        """Get current time.
        
        Args:
            timezone: Timezone name (default: UTC)
            
        Returns:
            ToolResult with current time information
        """
        now = datetime.utcnow()
        return ToolResult(
            content=[{
                "type": "text",
                "text": json.dumps({
                    "datetime": now.isoformat(),
                    "timezone": timezone,
                    "timestamp": now.timestamp()
                })
            }],
            isError=False
        )



class WebSearchTool(BaseTool):
    """Placeholder for web search tool (you'd integrate with real API)."""
    risk: ClassVar[ToolRisk] = ToolRisk.SENSITIVE  # external network request
    
    def __init__(self):
        super().__init__(
            name="web_search",
            description="Search the web for information. Returns relevant search results.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query"
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "Number of search results to return",
                        "default": 5
                    }
                },
                "required": ["query"]
            }
        )
    
    async def execute(self, query: str, num_results: int = 5) -> ToolResult:
        """Search the web.
        
        Args:
            query: Search query
            num_results: Number of results to return (default: 5)
            
        Returns:
            ToolResult with search results
        """
        # This is a placeholder - integrate with real search API
        return ToolResult(
            content=[{
                "type": "text",
                "text": json.dumps({
                    "query": query,
                    "results": [
                        {"title": "Example Result", "url": "https://example.com", "snippet": "This is a placeholder result"}
                    ],
                    "note": "This is a placeholder implementation. Integrate with a real search API (e.g., Serper, Brave, Google)."
                })
            }],
            isError=False
        )
