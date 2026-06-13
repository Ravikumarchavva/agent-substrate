from __future__ import annotations

import pytest
from ravi.kernel.core.content import (
    ChatMessage,
    TextBlock,
    CodeBlock,
    DataBlock,
    ErrorBlock,
    ImageBlock,
    ToolUseBlock,
    ToolResultBlock,
    content_blocks_to_str,
    content_block_from_dict,
)


def test_text_block():
    block = TextBlock(text="hello")
    assert block.type == "text"
    assert block.text == "hello"
    assert block.to_text_repr() == "hello"


def test_code_block():
    block = CodeBlock(code="print('hi')", language="python")
    assert block.type == "code"
    assert block.code == "print('hi')"
    assert block.language == "python"
    assert block.to_text_repr() == "```python\nprint('hi')\n```"


def test_data_block():
    block = DataBlock(data={"a": 1})
    assert block.type == "data"
    assert block.data == {"a": 1}
    assert block.to_text_repr() == '{"a": 1}'


def test_error_block():
    block = ErrorBlock(
        error_type="ValueError", message="invalid value", recoverable=True
    )
    assert block.type == "error"
    assert block.error_type == "ValueError"
    assert block.message == "invalid value"
    assert block.recoverable is True
    assert block.to_text_repr() == "[ValueError]: invalid value"


def test_image_block_url():
    block = ImageBlock(url="http://example.com/img.png")
    assert block.type == "image"
    assert block.url == "http://example.com/img.png"
    assert block.to_text_repr() == "[Image: http://example.com/img.png]"


def test_image_block_validation_error():
    with pytest.raises(
        ValueError, match="Exactly one of url, data, or file_id must be provided"
    ):
        ImageBlock(url="http://example.com/img.png", file_id="123")


def test_tool_use_block():
    block = ToolUseBlock(call_id="call1", tool_name="echo", arguments={"text": "hi"})
    assert block.type == "tool_use"
    assert block.call_id == "call1"
    assert block.tool_name == "echo"
    assert block.arguments == {"text": "hi"}
    assert block.to_text_repr() == "[ToolCall: echo(call1)]"


def test_tool_result_block():
    result = ToolResultBlock(
        call_id="call1", content=[TextBlock(text="done")], is_error=False
    )
    assert result.type == "tool_result"
    assert result.call_id == "call1"
    assert result.is_error is False
    assert result.to_text_repr() == "[ToolResult: call1] done"


def test_content_block_from_dict():
    raw = {"type": "text", "text": "hello dict"}
    block = content_block_from_dict(raw)
    assert isinstance(block, TextBlock)
    assert block.text == "hello dict"

    from ravi.kernel.core.content import UnknownBlock

    raw_error = {"type": "unknown", "text": "fallback"}
    block_fallback = content_block_from_dict(raw_error)
    assert isinstance(block_fallback, UnknownBlock)
    assert block_fallback.raw == raw_error


def test_content_blocks_to_str():
    blocks = [TextBlock(text="hello"), CodeBlock(code="x = 1")]
    res = content_blocks_to_str(blocks)
    assert "hello" in res
    assert "```python\nx = 1\n```" in res


def test_chat_message():
    msg = ChatMessage(role="user", content=[TextBlock(text="hi")])
    assert msg.role == "user"
    assert len(msg.content) == 1
    assert msg.content[0].text == "hi"
