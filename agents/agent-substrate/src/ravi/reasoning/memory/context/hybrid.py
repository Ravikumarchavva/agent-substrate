from __future__ import annotations

import json
from typing import TYPE_CHECKING, List, Optional

from ravi.reasoning.memory.context._helpers import split_system
from ravi.kernel.context.base_context import ModelContext
from ravi.kernel.messages.base_message import BaseClientMessage

if TYPE_CHECKING:
    from ravi.reasoning.memory.session import SessionManager
    from ravi.kernel.llm.base_client import BaseModelClient


class HybridContext(ModelContext):
    """Fuse hot in-process history with cold storage backfill via SessionManager."""

    def __init__(
        self,
        session_manager: "SessionManager",
        recent_n: int = 20,
        max_total: int = 40,
    ) -> None:
        if recent_n < 1 or max_total < 1:
            raise ValueError("recent_n and max_total must be >= 1")
        if recent_n > max_total:
            raise ValueError("recent_n cannot exceed max_total")
        self._session_manager = session_manager
        self.recent_n = recent_n
        self.max_total = max_total

    async def build(
        self,
        *,
        session_id: str,
        current_input: str,
        raw_messages: List[BaseClientMessage],
        model_client: Optional["BaseModelClient"] = None,
    ) -> List[BaseClientMessage]:
        system_msg, rest = split_system(raw_messages)
        recent = rest[-self.recent_n :] if len(rest) > self.recent_n else list(rest)

        combined = recent
        if len(combined) < self.max_total:
            try:
                needed = self.max_total - len(combined)
                cold_messages = await self._session_manager.get_messages(
                    session_id=session_id,
                    limit=needed + self.recent_n,
                )
                seen = {id(m) for m in combined}
                seen_serialized = {
                    json.dumps(
                        m.model_dump() if hasattr(m, "model_dump") else str(m),
                        sort_keys=True,
                    )
                    for m in combined
                }
                unique_cold: List[BaseClientMessage] = []
                for m in cold_messages:
                    if id(m) in seen:
                        continue
                    serialized = json.dumps(
                        m.model_dump() if hasattr(m, "model_dump") else str(m),
                        sort_keys=True,
                    )
                    if serialized in seen_serialized:
                        continue
                    seen.add(id(m))
                    seen_serialized.add(serialized)
                    unique_cold.append(m)

                combined = unique_cold[:needed] + combined
            except Exception:
                pass

        if system_msg is not None:
            return [system_msg, *combined]
        return combined

    def __repr__(self) -> str:
        return f"<HybridContext(recent_n={self.recent_n}, max_total={self.max_total})>"
