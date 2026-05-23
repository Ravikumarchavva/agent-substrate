from __future__ import annotations

import base64
import json
from ravi.core.messages._types import ImageContent, DocumentContent
from ravi.core.messages.content import (
    DocumentBlock,
    CodeBlock,
    DataBlock,
    ErrorBlock,
    TextBlock,
)
from ravi.core.messages.client_messages import UserMessage, ToolExecutionResultMessage
from ravi.core.tools.base_tool import ToolResult

from ravi.core.messages.encoders.anthropic import (
    encode_messages as encode_anthropic,
    _get_block_text as anthropic_get_block_text,
)
from ravi.core.messages.encoders.gemini import (
    encode_messages as encode_gemini,
    _get_block_text as gemini_get_block_text,
)
from ravi.core.messages.encoders.openai import (
    encode_messages as encode_openai,
)


def test_user_message_accepts_live_image_content_instance() -> None:
    image = ImageContent(data=b"fake-image-bytes", media_type="image/png")

    message = UserMessage(content=["what's in this invoice?", image])

    assert message.content[0] == "what's in this invoice?"
    assert isinstance(message.content[1], ImageContent)
    assert message.content[1].media_type == "image/png"


def test_document_content_validation_and_coercion() -> None:
    doc = DocumentContent(
        data=b"fake-pdf-data", media_type="application/pdf", filename="invoice.pdf"
    )
    message = UserMessage(content=["Analyze this:", doc])

    assert message.content[0] == "Analyze this:"
    assert isinstance(message.content[1], DocumentContent)
    assert message.content[1].filename == "invoice.pdf"
    assert message.content[1].media_type == "application/pdf"

    # Test coercion from dict representation
    message_dict = UserMessage(
        content=[
            "Read this:",
            {
                "type": "document",
                "data": base64.b64encode(b"hello").decode("utf-8"),
                "media_type": "application/pdf",
                "filename": "hello.pdf",
            },
        ]
    )
    assert isinstance(message_dict.content[1], DocumentContent)
    assert message_dict.content[1].filename == "hello.pdf"
    assert message_dict.content[1].data == b"hello"


def test_document_block_extraction_from_tool_result() -> None:
    # Set up a tool result that returns a DocumentBlock
    pdf_b64 = base64.b64encode(b"pdf-document-bytes").decode("utf-8")
    tool_result = ToolResult(
        content=[
            TextBlock(text="Report generated."),
            DocumentBlock(
                data=pdf_b64, media_type="application/pdf", filename="report.pdf"
            ),
        ]
    )

    msg = ToolExecutionResultMessage.from_tool_result(
        tool_result=tool_result,
        tool_call_id="call_123",
        tool_name="generate_report",
    )

    assert msg.media is not None
    assert len(msg.media) == 1
    assert isinstance(msg.media[0], DocumentContent)
    assert msg.media[0].filename == "report.pdf"
    assert msg.media[0].data == b"pdf-document-bytes"
    assert msg.media[0].media_type == "application/pdf"


def test_document_content_serialization_anthropic() -> None:
    doc = DocumentContent(
        data=b"pdf-bytes", media_type="application/pdf", filename="test.pdf"
    )
    msg = ToolExecutionResultMessage(
        tool_call_id="call_123",
        name="test_tool",
        content=[TextBlock(text="done")],
        media=[doc],
    )

    _, conversation = encode_anthropic([msg])
    assert len(conversation) == 1
    tool_result_block = conversation[0]["content"][0]
    assert tool_result_block["type"] == "tool_result"
    assert len(tool_result_block["content"]) == 2

    # Text block
    assert tool_result_block["content"][0]["text"] == "done"

    # Native Document block
    doc_block = tool_result_block["content"][1]
    assert doc_block["type"] == "document"
    assert doc_block["source"]["media_type"] == "application/pdf"
    assert doc_block["source"]["data"] == base64.b64encode(b"pdf-bytes").decode("utf-8")


def test_document_content_serialization_gemini() -> None:
    doc = DocumentContent(
        data=b"pdf-bytes", media_type="application/pdf", filename="test.pdf"
    )
    msg = ToolExecutionResultMessage(
        tool_call_id="call_123",
        name="test_tool",
        content=[TextBlock(text="done")],
        media=[doc],
    )

    _, contents = encode_gemini([msg])
    assert len(contents) == 1
    gemini_content = contents[0]
    assert len(gemini_content.parts) == 2

    # FunctionResponse Part
    assert gemini_content.parts[0].function_response is not None
    assert gemini_content.parts[0].function_response.name == "test_tool"
    assert gemini_content.parts[0].function_response.response == {"result": "done"}

    # Inline PDF Part
    pdf_part = gemini_content.parts[1]
    assert pdf_part.inline_data is not None
    assert pdf_part.inline_data.mime_type == "application/pdf"
    assert pdf_part.inline_data.data == b"pdf-bytes"


def test_document_content_serialization_openai() -> None:
    doc = DocumentContent(
        data=b"pdf-bytes", media_type="application/pdf", filename="test.pdf"
    )
    msg = ToolExecutionResultMessage(
        tool_call_id="call_123",
        name="test_tool",
        content=[TextBlock(text="done")],
        media=[doc],
    )

    _, input_items = encode_openai([msg])
    # OpenAI encodes media as user message text fallbacks
    assert len(input_items) == 2
    assert input_items[0]["type"] == "function_call_output"
    assert input_items[1]["type"] == "message"
    assert "[Document Attachment: test.pdf]" in input_items[1]["content"][1]["text"]


def test_block_text_upgraded_fallbacks() -> None:
    code = CodeBlock(code="print('hi')", language="python")
    data = DataBlock(data={"a": 1, "b": 2})
    error = ErrorBlock(error_type="ValueError", message="invalid input")
    doc = DocumentBlock(
        data="base64...", media_type="application/pdf", filename="invoice.pdf"
    )

    # Test Anthropic get_block_text upgrades
    assert "```python\nprint('hi')\n```" in anthropic_get_block_text(code)
    assert json.dumps({"a": 1, "b": 2}) in anthropic_get_block_text(data)
    assert "[ValueError]: invalid input" in anthropic_get_block_text(error)
    assert "[Document: invoice.pdf (application/pdf)]" in anthropic_get_block_text(doc)

    # Test Gemini get_block_text upgrades
    assert "```python\nprint('hi')\n```" in gemini_get_block_text(code)
    assert json.dumps({"a": 1, "b": 2}) in gemini_get_block_text(data)
    assert "[ValueError]: invalid input" in gemini_get_block_text(error)
    assert "[Document: invoice.pdf (application/pdf)]" in gemini_get_block_text(doc)
