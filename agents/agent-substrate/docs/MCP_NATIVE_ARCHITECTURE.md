# MCP-Native Architecture

## Overview

The agent framework has been redesigned to be **MCP-native** (Model Context Protocol), with OpenAI compatibility provided through adapters. This approach ensures first-class support for MCP tools and standardized tool execution across different model providers.

## Key Design Principles

1. **MCP as the Source of Truth**: Tool schemas, tool calls, and tool results follow MCP format internally
2. **Adapter Pattern**: OpenAI and other providers are supported through format adapters
3. **Structured Results**: Tools return structured `ToolResult` objects with content blocks and error flags
4. **Type Safety**: Pydantic models throughout for validation and serialization

## Core Components

### 1. Tool Schema (`Tool`)

**Location**: `src/agent_framework/tools/base_tool.py`

MCP-native tool schema definition:

```python
class Tool(BaseModel):
    """MCP-compatible tool schema."""
    name: str
    description: str
    inputSchema: Dict[str, Any]  # JSON Schema
    
    def to_mcp_format(self) -> Dict[str, Any]:
        """Return MCP format - native representation."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.inputSchema
        }
    
    def to_openai_format(self) -> Dict[str, Any]:
        """Convert to OpenAI tool format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.inputSchema
            }
        }
```

**Key Features**:
- `inputSchema` uses standard JSON Schema format
- Direct MCP compatibility
- OpenAI adapter for backward compatibility

### 2. Tool Result (`ToolResult`)

**Location**: `src/agent_framework/tools/base_tool.py`

Structured result from tool execution:

```python
class ToolResult(BaseModel):
    """Structured result from tool execution (MCP-compatible)."""
    content: List[Dict[str, Any]] = Field(default_factory=list)
    isError: bool = Field(default=False, alias="is_error")
```

**Content Block Types**:
- Text: `{"type": "text", "text": "..."}`
- Image: `{"type": "image", "data": "base64...", "mimeType": "image/png"}`
- Resource: `{"type": "resource", "resource": {"uri": "...", "text": "..."}}`

**Example**:
```python
# Success
ToolResult(
    content=[{"type": "text", "text": '{"result": 42}'}],
    isError=False
)

# Error
ToolResult(
    content=[{"type": "text", "text": '{"error": "Division by zero"}'}],
    isError=True
)
```

### 3. Tool Call (`ToolCall` & `ToolCallMessage`)

**Location**: 
- `src/agent_framework/tools/base_tool.py` - `ToolCall` Pydantic model
- `src/agent_framework/messages/client_messages.py` - `ToolCallMessage` for messages

```python
class ToolCall(BaseModel):
    """Represents a tool call instance."""
    id: str
    name: str
    arguments: Dict[str, Any]

class ToolCallMessage(BaseClientMessage):
    """Represents a single tool call (MCP-compatible)."""
    role: CLIENT_ROLES = "tool_call"
    id: str
    name: str
    arguments: Dict[str, Any]
    
    def to_mcp_format(self) -> Dict[str, Any]:
        return {"name": self.name, "arguments": self.arguments}
    
    def to_openai_format(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments)
            }
        }
```

### 4. Tool Execution Result Message (`ToolExecutionResultMessage`)

**Location**: `src/agent_framework/messages/client_messages.py`

```python
class ToolExecutionResultMessage(BaseClientMessage):
    """Tool execution result message (MCP-compatible)."""
    role: CLIENT_ROLES = "tool_response"
    tool_call_id: str
    name: Optional[str] = None
    content: List[Dict[str, Any]]  # MCP content blocks
    isError: bool = False
    
    @classmethod
    def from_tool_result(
        cls,
        tool_result: ToolResult,
        tool_call_id: str,
        tool_name: Optional[str] = None
    ) -> "ToolExecutionResultMessage":
        """Create from ToolResult."""
        return cls(
            tool_call_id=tool_call_id,
            name=tool_name,
            content=tool_result.content,
            isError=tool_result.isError
        )
    
    def to_openai_format(self) -> Dict[str, Any]:
        """Convert to OpenAI tool message format."""
        # Flatten content blocks to string
        text_parts = []
        for block in self.content:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            # Handle other types...
        
        return {
            "role": "tool",
            "tool_call_id": self.tool_call_id,
            "content": "\n".join(text_parts)
        }
```

### 5. Base Tool (`BaseTool`)

**Location**: `src/agent_framework/tools/base_tool.py`

Abstract base class for all tools:

```python
class BaseTool(ABC):
    """Base class for MCP-compatible tools."""
    
    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        """Execute tool and return structured result."""
        pass
    
    def get_schema(self) -> Tool:
        """Return MCP-native tool schema."""
        pass
    
    def get_openai_schema(self) -> Dict[str, Any]:
        """Get OpenAI-compatible schema."""
        return self.get_schema().to_openai_format()
    
    def get_mcp_schema(self) -> Dict[str, Any]:
        """Get MCP-compatible schema."""
        return self.get_schema().to_mcp_format()
```

## Implementation Examples

### Creating a Tool

```python
from agent_framework.tools.base_tool import BaseTool, Tool, ToolResult

class WeatherTool(BaseTool):
    def __init__(self):
        self.tool_schema = Tool(
            name="get_weather",
            description="Get current weather for a location",
            inputSchema={
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "City name or coordinates"
                    },
                    "units": {
                        "type": "string",
                        "enum": ["celsius", "fahrenheit"],
                        "default": "celsius"
                    }
                },
                "required": ["location"]
            }
        )
    
    async def execute(self, location: str, units: str = "celsius") -> ToolResult:
        try:
            # Call weather API
            data = await fetch_weather(location, units)
            return ToolResult(
                content=[{
                    "type": "text",
                    "text": json.dumps(data)
                }],
                isError=False
            )
        except Exception as e:
            return ToolResult(
                content=[{
                    "type": "text",
                    "text": json.dumps({"error": str(e)})
                }],
                isError=True
            )
    
    def get_schema(self) -> Tool:
        return self.tool_schema
```

### Agent Integration

The ReActAgent automatically:
1. Converts `Tool` schemas to OpenAI format when calling the model client
2. Executes tools and receives `ToolResult` objects
3. Converts `ToolResult` to `ToolExecutionResultMessage` using `from_tool_result()`
4. Adds result messages to memory

```python
# In ReActAgent.run()
tool_schemas = [t.get_schema().to_openai_format() for t in self.tools]

response = await self.model_client.generate(
    messages=messages,
    tools=tool_schemas,
    tool_choice="auto"
)

# After execution
tool_result = await tool.execute(**tc_args)
tool_msg = ToolMessage.from_tool_result(
    tool_result=tool_result,
    tool_call_id=call_id,
    tool_name=tc_name
)
self.memory.add_message(tool_msg)
```

## Migration Guide

### Updating Existing Tools

**Before (OpenAI-first)**:
```python
class MyTool(BaseTool):
    def get_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "my_tool",
                "description": "...",
                "parameters": {...}
            }
        }
    
    async def execute(self, **kwargs) -> str:
        return json.dumps({"result": "..."})
```

**After (MCP-native)**:
```python
class MyTool(BaseTool):
    def __init__(self):
        self.tool_schema = Tool(
            name="my_tool",
            description="...",
            inputSchema={...}  # JSON Schema
        )
    
    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(
            content=[{"type": "text", "text": json.dumps({"result": "..."})}],
            isError=False
        )
    
    def get_schema(self) -> Tool:
        return self.tool_schema
```

### Updating Message Handling

**Before**:
```python
tool_msg = ToolMessage(
    content=json.dumps(result),
    tool_call_id=call_id,
    name=tool_name
)
```

**After**:
```python
tool_result = await tool.execute(**args)
tool_msg = ToolExecutionResultMessage.from_tool_result(
    tool_result=tool_result,
    tool_call_id=call_id,
    tool_name=tool_name
)
```

## Benefits

1. **Standardization**: Single source of truth for tool definitions
2. **Interoperability**: Native MCP support enables integration with MCP-compatible systems
3. **Flexibility**: Easy to add new model providers via adapters
4. **Type Safety**: Pydantic validation catches errors early
5. **Rich Content**: Support for text, images, and resources in tool results
6. **Error Handling**: Structured error reporting with `isError` flag
7. **Future-Proof**: Aligned with emerging standards

## References

- [Model Context Protocol Specification](https://modelcontextprotocol.io/)
- [Tool Schema JSON Schema](https://json-schema.org/)
- [OpenAI Function Calling](https://platform.openai.com/docs/guides/function-calling)
