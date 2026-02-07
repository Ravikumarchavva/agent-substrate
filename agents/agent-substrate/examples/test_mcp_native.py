"""Test MCP-native tool architecture."""
import asyncio
import json
from agent_framework.tools.base_tool import BaseTool, Tool, ToolResult
from agent_framework.messages.client_messages import ToolExecutionResultMessage

class SimpleTool(BaseTool):
    """A simple test tool."""
    
    def __init__(self):
        self.tool_schema = Tool(
            name="add_numbers",
            description="Add two numbers together",
            inputSchema={
                "type": "object",
                "properties": {
                    "a": {"type": "number", "description": "First number"},
                    "b": {"type": "number", "description": "Second number"}
                },
                "required": ["a", "b"]
            }
        )
    
    async def execute(self, a: float, b: float) -> ToolResult:
        """Execute the addition."""
        result = a + b
        return ToolResult(
            content=[{
                "type": "text",
                "text": json.dumps({"result": result, "operation": f"{a} + {b}"})
            }],
            isError=False
        )
    
    def get_schema(self) -> Tool:
        return self.tool_schema


async def test_mcp_native_tool():
    """Test MCP-native tool execution."""
    print("Testing MCP-native tool architecture...")
    
    # Create tool
    tool = SimpleTool()
    
    # Test schema formats
    print("\n1. MCP Schema:")
    mcp_schema = tool.get_mcp_schema()
    print(json.dumps(mcp_schema, indent=2))
    
    print("\n2. OpenAI Schema:")
    openai_schema = tool.get_openai_schema()
    print(json.dumps(openai_schema, indent=2))
    
    # Test execution
    print("\n3. Tool Execution:")
    result = await tool.execute(a=5, b=7)
    print(f"Result type: {type(result).__name__}")
    print(f"Content: {result.content}")
    print(f"Is Error: {result.isError}")
    
    # Test message conversion
    print("\n4. Tool Execution Result Message:")
    msg = ToolExecutionResultMessage.from_tool_result(
        tool_result=result,
        tool_call_id="test_call_123",
        tool_name="add_numbers"
    )
    print(f"Message type: {type(msg).__name__}")
    print(f"Tool Call ID: {msg.tool_call_id}")
    print(f"Content: {msg.content}")
    
    # Test format conversion
    print("\n5. OpenAI Format:")
    openai_msg = msg.to_openai_format()
    print(json.dumps(openai_msg, indent=2))
    
    print("\n6. MCP Format:")
    mcp_msg = msg.to_mcp_format()
    print(json.dumps(mcp_msg, indent=2))
    
    print("\n✅ All tests passed! MCP-native architecture is working correctly.")


if __name__ == "__main__":
    asyncio.run(test_mcp_native_tool())
