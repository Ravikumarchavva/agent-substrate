"""Chat wire-event helpers — image payloads for multimodal messages.

Split out of ``chat.py``. Inline persistence (``_WirePersister``,
``_build_tool_meta_map``) was removed: the agent itself durably logs its own
conversation straight to the EventLogProtocol (the single source of truth for
conversation history — see ``serving/stream/history.py::project_thread()``),
so no separate steps-table write is needed here anymore.
"""

from __future__ import annotations

from dataclasses import dataclass

from substrate.kernel.core.content import ImageBlock, TextBlock


@dataclass
class _ImagePayload:
    """Raw image binary for multimodal user messages."""

    data: bytes
    media_type: str


MediaType = str | _ImagePayload


def build_user_blocks(
    text: str, image_inputs: list[_ImagePayload]
) -> list[TextBlock | ImageBlock]:
    """Assemble the content blocks for a user turn — text plus any attached
    images. This is the one place that must include ``image_inputs``: a
    caller that resolves a vision model but forgets this step silently
    strips every uploaded image, since nothing else attaches them to the
    message the agent actually sees."""
    blocks: list[TextBlock | ImageBlock] = [TextBlock(text=text)]
    blocks.extend(
        ImageBlock(data=img.data, media_type=img.media_type) for img in image_inputs
    )
    return blocks


__all__ = [
    "_ImagePayload",
    "MediaType",
    "build_user_blocks",
]
