from abc import ABC, abstractmethod
from typing import Any, Optional
from pydantic import BaseModel, Field


class ToolParameter(BaseModel):
    """Represents a single parameter in a tool's schema."""
    type: str
    description: str
    enum: Optional[list[Any]] = None
    items: Optional[dict[str, Any]] = None  # For array types
    properties: Optional[dict[str, Any]] = None  # For object types
    required: Optional[list[str]] = None


class ToolSchema(BaseModel):
    """JSON Schema for tool parameters."""
    type: str = "object"
    properties: dict[str, dict[str, Any]]
    required: list[str] = Field(default_factory=list)
    
    class Config:
        arbitrary_types_allowed = True


class BaseTool(ABC):
    """Base class for all tools with OpenAI function calling schema."""
    
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
    
    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        """Execute the tool with given parameters.
        
        Args:
            **kwargs: Tool parameters
            
        Returns:
            Tool execution result (will be converted to string for LLM)
        """
        pass
    
    @abstractmethod
    def get_schema(self) -> dict[str, Any]:
        """Return OpenAI function calling schema.
        
        Returns:
            Dictionary with 'type', 'function' keys following OpenAI format:
            {
                "type": "function",
                "function": {
                    "name": "tool_name",
                    "description": "Tool description",
                    "parameters": {
                        "type": "object",
                        "properties": {...},
                        "required": [...]
                    }
                }
            }
        """
        pass
    
    def __str__(self) -> str:
        return f"{self.name}: {self.description}"
    
    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(name='{self.name}')>"