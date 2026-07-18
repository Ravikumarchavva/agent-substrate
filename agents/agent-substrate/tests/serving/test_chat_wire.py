"""build_user_blocks — the message content the agent actually receives must
include attached images, not just text.

Regression test for a real bug: chat.py used to compute an image-carrying
`user_input_content` list that nothing ever read, so an uploaded image was
never attached to the user turn even though a vision model was correctly
resolved for the request — the model always saw text-only content."""

from __future__ import annotations

from substrate.kernel.core.content import ImageBlock, TextBlock
from substrate.serving.monolith.routes.chat_wire import _ImagePayload, build_user_blocks


def test_build_user_blocks_text_only():
    blocks = build_user_blocks("hello", [])
    assert blocks == [TextBlock(text="hello")]


def test_build_user_blocks_includes_images():
    payload = _ImagePayload(data=b"\x89PNG...", media_type="image/png")
    blocks = build_user_blocks("what's in this image", [payload])

    assert len(blocks) == 2
    assert isinstance(blocks[0], TextBlock)
    assert blocks[0].text == "what's in this image"
    assert isinstance(blocks[1], ImageBlock)
    assert blocks[1].data == b"\x89PNG..."
    assert blocks[1].media_type == "image/png"


def test_build_user_blocks_multiple_images_preserve_order():
    payloads = [
        _ImagePayload(data=b"first", media_type="image/png"),
        _ImagePayload(data=b"second", media_type="image/jpeg"),
    ]
    blocks = build_user_blocks("compare these", payloads)

    assert len(blocks) == 3
    images = [b for b in blocks if isinstance(b, ImageBlock)]
    assert [img.data for img in images] == [b"first", b"second"]
