"""Chat wire-event helpers — image payloads for multimodal messages.

Split out of ``chat.py``. Inline persistence (``_WirePersister``,
``_build_tool_meta_map``) was removed: the agent itself durably logs its own
conversation straight to the EventLog (the single source of truth for
conversation history — see ``serving/stream/history.py::project_thread()``),
so no separate steps-table write is needed here anymore.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class _ImagePayload:
    """Raw image binary for multimodal user messages."""

    data: bytes
    media_type: str


MediaType = str | _ImagePayload


__all__ = [
    "_ImagePayload",
    "MediaType",
]
