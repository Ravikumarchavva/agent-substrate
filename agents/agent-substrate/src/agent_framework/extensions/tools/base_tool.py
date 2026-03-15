from abc import ABC, abstractmethod
from typing import Any, Optional, Dict, List
from pydantic import BaseModel, Field, ConfigDict, field_validator
import json
from uuid import uuid4

class Tool(BaseModel):
    """MCP-compatible tool schema with annotations and MCP Apps UI support."""
    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)
    
    name: str
    description: str
    inputSchema: Dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}, "required": []},
        description="JSON Schema for tool input parameters (MCP format)"
    )
    annotations: Optional[Dict[str, Any]] = Field(
        default=None,
        description="MCP tool annotations (readOnlyHint, destructiveHint, openWorldHint, title)"
    )
    meta: Optional[Dict[str, Any]] = Field(
        default=None,
        alias="_meta",
        serialization_alias="_meta",
        description="MCP Apps metadata — e.g. {'ui': {'resourceUri': 'ui://...'}}"
    )
    
    def to_openai_format(self) -> Dict[str, Any]:
        """Convert MCP tool schema to OpenAI function calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.inputSchema,
                "strict": True
            }
        }
    
    def to_mcp_format(self) -> Dict[str, Any]:
        """Export as MCP tool schema with annotations and _meta."""
        result: Dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.inputSchema
        }
        if self.annotations:
            result["annotations"] = self.annotations
        if self.meta:
            result["_meta"] = self.meta
        return result

class ToolResult(BaseModel):
    """Structured result from tool execution (MCP-compatible)."""
    content: List[Dict[str, Any]] = Field(default_factory=list)
    isError: bool = Field(default=False, alias="is_error")
    app_data: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Structured data for MCP App UIs (passed to iframe, not to LLM)",
    )
    
    model_config = ConfigDict(populate_by_name=True)

class ToolCall(BaseModel):
    """Represents a tool call instance."""
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)

    @field_validator('arguments', mode='before')
    def _validate_arguments(cls, v: Any) -> Dict[str, Any]:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                raise ValueError("arguments must be a dict or JSON string")
        if isinstance(v, dict):
            return v
        raise ValueError("arguments must be a dict")

class BaseTool(ABC):
    """Base class for MCP-compatible tools with optional MCP Apps UI support."""
    
    def __init__(
        self,
        name: str,
        description: str,
        input_schema: Optional[Dict[str, Any]] = None,
        annotations: Optional[Dict[str, Any]] = None,
        _meta: Optional[Dict[str, Any]] = None,
    ):
        self.name = name
        self.description = description
        self.input_schema = input_schema or {
            "type": "object",
            "properties": {},
            "required": []
        }
        self.annotations = annotations
        self._meta = _meta
    
    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        """Execute the tool with given parameters.
        
        Args:
            **kwargs: Tool parameters validated against input_schema
            
        Returns:
            ToolResult with structured content and error flag
        """
        pass
    
    def get_schema(self) -> Tool:
        """Return MCP-native tool schema."""
        return Tool(
            name=self.name,
            description=self.description,
            inputSchema=self.input_schema,
            annotations=getattr(self, 'annotations', None),
            meta=getattr(self, '_meta', None),
        )
    
    def get_openai_schema(self) -> Dict[str, Any]:
        """Return OpenAI function calling format (compatibility adapter).
        
        Returns:
            Dictionary with 'type', 'function' keys following OpenAI format
        """
        return self.get_schema().to_openai_format()
    
    def get_mcp_schema(self) -> Dict[str, Any]:
        """Return MCP tool schema format.
        
        Returns:
            Dictionary with 'name', 'description', 'inputSchema' (MCP native)
        """
        return self.get_schema().to_mcp_format()
    
    def __str__(self) -> str:
        return f"{self.name}: {self.description}"
    
    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(name='{self.name}')>"