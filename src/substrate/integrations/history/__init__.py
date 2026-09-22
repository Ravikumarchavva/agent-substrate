"""substrate.capabilities.history — Durable HistoryProvider backends.

``LocalFilesystemHistoryProvider`` lives in ``agents.storage`` — pure
kernel+stdlib, no durable-infra dependency, so it belongs at L1 beside
``InMemoryHistoryProvider``. This package now holds only backends with a
real L2 dependency (Postgres).
"""

from __future__ import annotations

from substrate.capabilities.history.durable_history import (
    DurableHistoryProvider,
    HistoryMessage,
    HistorySession,
)

__all__ = [
    "DurableHistoryProvider",
    "HistorySession",
    "HistoryMessage",
]
