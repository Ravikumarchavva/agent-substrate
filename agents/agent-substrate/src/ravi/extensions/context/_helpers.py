from __future__ import annotations

import json
from typing import List, Optional

from ravi.kernel.messages.base_message import BaseClientMessage
from ravi.kernel.messages.client_messages import SystemMessage


def estimate_tokens(messages: List[BaseClientMessage]) -> int:
    """Very rough token estimate: 4 chars ~= 1 token."""
    total = 0
    for msg in messages:
        total += (
            len(json.dumps(msg.model_dump() if hasattr(msg, "model_dump") else str(msg)))
            // 4
        )
    return total


def split_system(
    messages: List[BaseClientMessage],
) -> tuple[Optional[BaseClientMessage], List[BaseClientMessage]]:
    """Separate the first SystemMessage from the rest."""
    if messages and isinstance(messages[0], SystemMessage):
        return messages[0], messages[1:]
    return None, messages
