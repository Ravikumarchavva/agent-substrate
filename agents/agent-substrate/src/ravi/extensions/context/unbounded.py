from __future__ import annotations

from typing import List, Optional, TYPE_CHECKING

from ravi.kernel.context.base_context import ModelContext
from ravi.kernel.messages.base_message import BaseClientMessage

if TYPE_CHECKING:
    from ravi.kernel.llm.base_client import BaseModelClient


class UnboundedContext(ModelContext):
    """Pass-through context that returns all messages unchanged."""

    async def build(
        self,
        *,
        session_id: str,
        current_input: str,
        raw_messages: List[BaseClientMessage],
        model_client: Optional["BaseModelClient"] = None,
    ) -> List[BaseClientMessage]:
        return raw_messages

    def __repr__(self) -> str:
        return "<UnboundedContext>"
