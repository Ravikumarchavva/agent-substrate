"""substrate.integrations.memory — Concrete memory backends.

Memory itself — long-term records and per-session state — lives in the store (``Store.memory``, ``Store.session_state``).
What is here is optional: a Redis cache for session state, and what manages and exposes memory to an agent.

Short-term memory (ShortTermMemory protocol):
    RedisSessionStore      — Redis HASH per session, configurable TTL
    CachedShortTermMemory  — a durable primary (the store's) with a fast cache in front
"""

from __future__ import annotations

from substrate.integrations.memory.redis_session_store import RedisSessionStore
from substrate.integrations.memory.cached_session_store import CachedShortTermMemory
from substrate.integrations.memory.policy import (
    MemoryExposurePolicy,
    DefaultMemoryExposurePolicy,
)
from substrate.integrations.memory.manager import MemoryManager

__all__ = [
    "RedisSessionStore",
    "CachedShortTermMemory",
    "MemoryExposurePolicy",
    "DefaultMemoryExposurePolicy",
    "MemoryManager",
]
