from __future__ import annotations

import pytest
from substrate.kernel.exceptions import BlockValidationError, KernelError
from substrate.kernel.core.content import (
    ChatMessage,
    DataBlock,
    ErrorBlock,
    KernelModel,
    MediaBlock,
    ReasoningBlock,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UnknownBlock,
    content_blocks_to_str,
    parse_content_block,
)


def test_base_model_immutability():
    block = TextBlock(text="immutable")
    assert isinstance(block, KernelModel)
    with pytest.raises(Exception):
        block.text = "modified"  # type: ignore[misc]

    img = MediaBlock.image(url="http://example.com/img.png")
    assert isinstance(img, MediaBlock)
    assert isinstance(img, KernelModel)


def test_text_block():
    block = TextBlock(text="hello")
    assert block.type == "text"
    assert block.text == "hello"
    assert str(block) == "hello"


def test_data_block():
    block = DataBlock(data={"a": 1})
    assert block.type == "data"
    assert block.data == {"a": 1}
    assert str(block) == '{"a": 1}'


def test_error_block():
    block = ErrorBlock(
        error_type="ValueError", message="invalid value", recoverable=True
    )
    assert block.type == "error"
    assert block.error_type == "ValueError"
    assert block.message == "invalid value"
    assert block.recoverable is True
    assert str(block) == "[ValueError]: invalid value"


def test_media_block_url():
    block = MediaBlock.image(url="http://example.com/img.png")
    assert block.type == "image"
    assert block.is_image is True
    assert block.url == "http://example.com/img.png"
    assert str(block) == "[Image: http://example.com/img.png]"


def test_media_block_validation_error():
    with pytest.raises(
        ValueError, match="Exactly one of url, data, or file_id must be provided"
    ):
        MediaBlock.image(url="http://example.com/img.png", file_id="123")


def test_media_blocks_centralized_validation():
    # Audio media block
    audio = MediaBlock.audio(url="http://example.com/audio.wav")
    assert isinstance(audio, MediaBlock)
    assert audio.is_audio is True
    assert str(audio) == "[Audio: http://example.com/audio.wav]"
    with pytest.raises(
        ValueError, match="Exactly one of url, data, or file_id must be provided"
    ):
        MediaBlock(type="audio")  # None provided
    with pytest.raises(
        ValueError, match="Exactly one of url, data, or file_id must be provided"
    ):
        MediaBlock.audio(url="http://example.com/a.wav", file_id="f1")  # Multiple provided

    # Video media block
    video = MediaBlock.video(url="http://example.com/video.mp4")
    assert isinstance(video, MediaBlock)
    assert video.is_video is True
    assert str(video) == "[Video: http://example.com/video.mp4]"

    # Document media block
    doc = MediaBlock.document(file_id="doc-123", filename="paper.pdf")
    assert isinstance(doc, MediaBlock)
    assert doc.is_document is True
    assert str(doc) == "[Document: paper.pdf]"


def test_tool_use_block():
    block = ToolUseBlock(call_id="call1", tool_name="echo", arguments={"text": "hi"})
    assert block.type == "tool_use"
    assert block.call_id == "call1"
    assert block.tool_name == "echo"
    assert block.arguments == {"text": "hi"}
    assert str(block) == "[ToolCall: echo(call1)]"


def test_tool_result_block():
    result = ToolResultBlock(
        call_id="call1", content=[TextBlock(text="done")], is_error=False
    )
    assert result.type == "tool_result"
    assert result.call_id == "call1"
    assert result.is_error is False
    assert result.text == "done"
    assert str(result) == "[ToolResult: call1] done"


def test_tool_result_block_string_coercion():
    result = ToolResultBlock(call_id="call1", content="direct string result")
    assert len(result.content) == 1
    assert isinstance(result.content[0], TextBlock)
    assert result.text == "direct string result"


def test_reasoning_block():
    block = ReasoningBlock(text="let me think", signature="sig-abc")
    assert block.type == "reasoning"
    assert block.text == "let me think"
    assert block.signature == "sig-abc"
    assert block.redacted is False
    assert str(block) == "[Reasoning] let me think"


def test_reasoning_block_redacted():
    block = ReasoningBlock(text="opaque-payload", redacted=True)
    assert block.redacted is True
    assert str(block) == "[Reasoning: redacted]"


def test_reasoning_block_round_trips_through_registry():
    block = ReasoningBlock(text="chain of thought", signature="sig-xyz")
    restored = parse_content_block(block.model_dump(mode="json"))
    assert isinstance(restored, ReasoningBlock)
    assert restored.text == "chain of thought"
    assert restored.signature == "sig-xyz"


def test_parse_content_block():
    raw = {"type": "text", "text": "hello dict"}
    block = parse_content_block(raw)
    assert isinstance(block, TextBlock)
    assert block.text == "hello dict"

    raw_unknown = {"type": "future_block", "some_data": "123"}
    block_unknown = parse_content_block(raw_unknown)
    assert isinstance(block_unknown, UnknownBlock)
    assert block_unknown.raw == raw_unknown

    # Verify NO double wrapping on already-serialized unknown blocks:
    dumped = block_unknown.model_dump(mode="json")
    restored = parse_content_block(dumped)
    assert isinstance(restored, UnknownBlock)
    assert restored.raw == raw_unknown  # Not nested under 'raw' again!


def test_block_validation_error():
    bad = {"type": "text"}  # missing text
    with pytest.raises(BlockValidationError) as exc_info:
        parse_content_block(bad)
    assert isinstance(exc_info.value, KernelError)
    assert isinstance(exc_info.value, ValueError)


def test_content_blocks_to_str():
    blocks = [TextBlock(text="hello"), TextBlock(text="world")]
    res = content_blocks_to_str(blocks)
    assert "hello\nworld" == res


def test_chat_message():
    msg = ChatMessage(role="user", content=[TextBlock(text="hi")])
    assert msg.role == "user"
    assert len(msg.content) == 1
    assert msg.text == "hi"


def test_chat_message_string_coercion():
    msg = ChatMessage(role="user", content="hello string")
    assert len(msg.content) == 1
    assert isinstance(msg.content[0], TextBlock)
    assert msg.text == "hello string"


def test_chat_message_role_construction():
    u = ChatMessage(role=Role.USER, content="user prompt")
    assert u.role == "user"
    assert u.text == "user prompt"

    a = ChatMessage(role="assistant", content="assistant answer")
    assert a.role == "assistant"
    assert a.text == "assistant answer"

    s = ChatMessage(role=Role.SYSTEM, content="system instructions")
    assert s.role == "system"
    assert s.text == "system instructions"


def test_chat_message_with_unknown_block():
    unknown = UnknownBlock(raw={"type": "3d_spatial_canvas", "coords": [1, 2, 3]})
    msg = ChatMessage(role="assistant", content=[unknown])
    assert len(msg.content) == 1
    assert isinstance(msg.content[0], UnknownBlock)

    dumped = msg.model_dump_json()
    restored = ChatMessage.model_validate_json(dumped)
    assert isinstance(restored.content[0], UnknownBlock)
    assert restored.content[0].raw == {"type": "3d_spatial_canvas", "coords": [1, 2, 3]}
