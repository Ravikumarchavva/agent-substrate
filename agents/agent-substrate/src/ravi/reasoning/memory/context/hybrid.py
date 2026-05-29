from __future__ import annotations

import json
from typing import TYPE_CHECKING, List, Optional

from ravi.reasoning.memory.context._helpers import split_system
from ravi.kernel.context.compaction import CompactionStrategy, Trigger
from ravi.kernel.messages.base_message import BaseClientMessage

if TYPE_CHECKING:
    from ravi.kernel.memory.history_provider import HistoryProvider
    from ravi.kernel.llm.base_client import BaseModelClient


class HybridStrategy(CompactionStrategy):
    """Fuse hot recent history with cold-storage backfill via a HistoryProvider."""

    trigger = Trigger.BEFORE_LLM_CALL

    def __init__(
        self,
        provider: "HistoryProvider",
        recent_n: int = 20,
        max_total: int = 40,
    ) -> None:
        if recent_n < 1 or max_total < 1:
            raise ValueError("recent_n and max_total must be >= 1")
        if recent_n > max_total:
            raise ValueError("recent_n cannot exceed max_total")
        self._provider = provider
        self.recent_n = recent_n
        self.max_total = max_total

    async def apply(
        self,
        messages: List[BaseClientMessage],
        session_id: str,
        history: "HistoryProvider",
        model_client: Optional["BaseModelClient"] = None,
    ) -> List[BaseClientMessage]:
        system_msg, rest = split_system(messages)
        recent = rest[-self.recent_n:] if len(rest) > self.recent_n else list(rest)

        combined = recent
        if len(combined) < self.max_total:
            try:
                needed = self.max_total - len(combined)
                cold_messages = await self._provider.load_messages(
                    session_id,
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
        return f"<HybridStrategy(recent_n={self.recent_n}, max_total={self.max_total})>"
