from __future__ import annotations

from typing import List, Optional, TYPE_CHECKING

from ravi.extensions.context._helpers import split_system
from ravi.kernel.context.base_context import ModelContext
from ravi.kernel.messages.base_message import BaseClientMessage

if TYPE_CHECKING:
    from ravi.kernel.llm.base_client import BaseModelClient


class SlidingWindowContext(ModelContext):
    """Keep system prompt plus the last max_messages non-system messages."""

    def __init__(self, max_messages: int = 20) -> None:
        if max_messages < 1:
            raise ValueError("max_messages must be >= 1")
        self.max_messages = max_messages

    async def build(
        self,
        *,
        session_id: str,
        current_input: str,
        raw_messages: List[BaseClientMessage],
        model_client: Optional["BaseModelClient"] = None,
    ) -> List[BaseClientMessage]:
        system_msg, rest = split_system(raw_messages)
        windowed = rest[-self.max_messages :] if len(rest) > self.max_messages else rest
        if system_msg is not None:
            return [system_msg, *windowed]
        return windowed

    def __repr__(self) -> str:
        return f"<SlidingWindowContext(max_messages={self.max_messages})>"
