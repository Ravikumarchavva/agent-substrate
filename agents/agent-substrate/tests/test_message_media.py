from __future__ import annotations

from ravi.core.messages._types import ImageContent
from ravi.core.messages.client_messages import UserMessage


def test_user_message_accepts_live_image_content_instance() -> None:
    image = ImageContent(data=b"fake-image-bytes", media_type="image/png")

    message = UserMessage(content=["what's in this invoice?", image])

    assert message.content[0] == "what's in this invoice?"
    assert isinstance(message.content[1], ImageContent)
    assert message.content[1].media_type == "image/png"
