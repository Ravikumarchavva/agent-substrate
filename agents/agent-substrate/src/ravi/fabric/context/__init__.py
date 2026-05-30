"""Context and Memory Management Engine.

This module provides the core abstractions for agent state management:
- HistoryProvider: Durable storage for raw messages.
- CompactionStrategy: Policies for converting raw history into an LLM context window.
- AgentContext: The runtime environment that bridges history, tools, and budget for an agent.
"""

from .history import HistoryProvider, InMemoryHistoryProvider
from .compaction import CompactionStrategy, SlidingWindowCompaction
from .context import AgentContext, DefaultAgentContext

__all__ = [
    "HistoryProvider",
    "InMemoryHistoryProvider",
    "CompactionStrategy",
    "SlidingWindowCompaction",
    "AgentContext",
    "DefaultAgentContext",
]
