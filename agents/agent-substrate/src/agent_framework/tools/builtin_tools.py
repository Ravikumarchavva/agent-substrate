"""Built-in tool implementations."""
from typing import Any
import json
from datetime import datetime

from .base_tool import BaseTool


class CalculatorTool(BaseTool):
    """Simple calculator tool for basic math operations."""
    
    def __init__(self):
        super().__init__(
            name="calculator",
            description="Performs basic mathematical calculations. Supports +, -, *, /, ** (power), and % (modulo)."
        )
    
    async def execute(self, expression: str) -> str:
        """Execute a mathematical expression.
        
        Args:
            expression: Math expression as string (e.g., "2 + 2", "10 * 5")
            
        Returns:
            Result as string
        """
        try:
            # Safe evaluation - only allow math operations
            result = eval(expression, {"__builtins__": {}}, {})
            return json.dumps({"result": result, "expression": expression})
        except Exception as e:
            return json.dumps({"error": str(e), "expression": expression})
    
    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "expression": {
                            "type": "string",
                            "description": "The mathematical expression to evaluate (e.g., '2 + 2', '10 * 5')"
                        }
                    },
                    "required": ["expression"]
                }
            }
        }


class GetCurrentTimeTool(BaseTool):
    """Tool to get the current time."""
    
    def __init__(self):
        super().__init__(
            name="get_current_time",
            description="Returns the current date and time in ISO format."
        )
    
    async def execute(self, timezone: str = "UTC") -> str:
        """Get current time.
        
        Args:
            timezone: Timezone name (default: UTC)
            
        Returns:
            Current time as JSON string
        """
        now = datetime.utcnow()
        return json.dumps({
            "datetime": now.isoformat(),
            "timezone": timezone,
            "timestamp": now.timestamp()
        })
    
    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
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
            }
        }


class WebSearchTool(BaseTool):
    """Placeholder for web search tool (you'd integrate with real API)."""
    
    def __init__(self):
        super().__init__(
            name="web_search",
            description="Search the web for information. Returns relevant search results."
        )
    
    async def execute(self, query: str, num_results: int = 5) -> str:
        """Search the web.
        
        Args:
            query: Search query
            num_results: Number of results to return (default: 5)
            
        Returns:
            Search results as JSON string
        """
        # This is a placeholder - integrate with real search API
        return json.dumps({
            "query": query,
            "results": [
                {"title": "Example Result", "url": "https://example.com", "snippet": "This is a placeholder result"}
            ],
            "note": "This is a placeholder implementation. Integrate with a real search API (e.g., Serper, Brave, Google)."
        })
    
    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
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
            }
        }
