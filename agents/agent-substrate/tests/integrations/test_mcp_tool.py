from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, Mock
from ravi.integrations.tools.mcp.tool import MCPTool
from ravi.kernel.content import TextBlock


@pytest.mark.asyncio
async def test_mcp_tool_wrapping_and_execution():
    # Mock MCPClient
    client = Mock()
    client.is_connected = True
    
    # Mock native MCP SDK response
    mock_mcp_item = Mock()
    mock_mcp_item.type = "text"
    mock_mcp_item.text = "hello from mcp"
    
    mock_mcp_response = Mock()
    mock_mcp_response.content = [mock_mcp_item]
    mock_mcp_response.isError = False
    
    client.call_tool = AsyncMock(return_value=mock_mcp_response)

    schema = {
        "type": "object",
        "properties": {"arg": {"type": "string"}},
        "required": ["arg"]
    }

    tool = MCPTool(
        client=client,
        name="test_mcp_tool",
        description="mcp tool description",
        input_schema=schema,
    )

    assert tool.name == "test_mcp_tool"
    assert tool.description == "mcp tool description"
    assert tool.input_schema == schema

    # Test execute
    res = await tool.execute(arg="value")
    assert res.is_error is False
    assert len(res.content) == 1
    assert isinstance(res.content[0], TextBlock)
    assert res.content[0].text == "hello from mcp"
    client.call_tool.assert_called_once_with("test_mcp_tool", {"arg": "value"})
