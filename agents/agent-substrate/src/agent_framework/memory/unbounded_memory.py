"""Unbounded memory implementation that stores all messages."""
from typing import Optional
import json

from .base_memory import BaseMemory
from agent_framework.messages.base_message import BaseClientMessage


class UnboundedMemory(BaseMemory):
    """Simple memory that stores all messages without limit.
    
    Warning: This can grow indefinitely and cause context window issues.
    Use BoundedMemory or SlidingWindowMemory for production.
    """
    
    def __init__(self):
        self._messages: list[BaseClientMessage] = []

    def add_message(self, message: BaseClientMessage) -> None:
        """Add a message to memory."""
        self._messages.append(message)

    def get_messages(self, limit: Optional[int] = None) -> list[BaseClientMessage]:
        """Retrieve messages from memory.
        
        Args:
            limit: If provided, return only the last N messages
        """
        if limit is None:
            return self._messages.copy()
        return self._messages[-limit:] if limit > 0 else []

    def clear(self) -> None:
        """Clear all messages."""
        self._messages = []
    
    def get_token_count(self) -> int:
        """Approximate token count (very rough estimate)."""
        total = 0
        for msg in self._messages:
            # Rough estimate: 4 chars ≈ 1 token
            content_str = str(msg.content)
            total += len(content_str) // 4
        return total
    
    def __repr__(self) -> str:
        return f"<UnboundedMemory(messages={len(self._messages)}, tokens≈{self.get_token_count()})>"