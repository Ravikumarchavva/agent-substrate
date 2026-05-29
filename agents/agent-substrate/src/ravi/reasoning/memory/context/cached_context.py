from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from ravi.reasoning.memory.context._helpers import split_system
from ravi.kernel.context.base_context import ModelContext
from ravi.kernel.messages.base_message import BaseClientMessage

if TYPE_CHECKING:
    from ravi.integrations.memory.redis_memory import RedisMemory
    from ravi.kernel.llm.base_client import BaseModelClient


class RedisModelContext(ModelContext):
    """Context strategy that reads directly from RedisMemory cache."""

    def __init__(
        self,
        redis_memory: "RedisMemory",
        recent_n: int = 10,
    ) -> None:
        if recent_n < 1:
            raise ValueError("recent_n must be >= 1")
        self._redis_memory = redis_memory
        self.recent_n = recent_n

    async def build(
        self,
        *,
        session_id: str,
        current_input: str,
        raw_messages: List[BaseClientMessage],
        model_client: Optional["BaseModelClient"] = None,
    ) -> List[BaseClientMessage]:
        all_messages = await self._redis_memory.get_messages()
        system_msg, rest = split_system(all_messages)
        windowed = rest[-self.recent_n :] if len(rest) > self.recent_n else list(rest)
        if system_msg is not None:
            return [system_msg, *windowed]
        return windowed

    def __repr__(self) -> str:
        return (
            f"<RedisModelContext(session_id={self._redis_memory.session_id!r}, "
            f"recent_n={self.recent_n})>"
        )
