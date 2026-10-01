"""substrate.integrations.history — Durable HistoryProvider backends.

``LocalFilesystemHistoryProvider`` lives in ``kernel.storage`` — pure stdlib, the zero-infra
default. This package holds the backends with a real infrastructure dependency (Postgres).
"""

from __future__ import annotations

from substrate.integrations.history.durable_history import (
    DurableHistoryProvider,
    HistoryMessage,
    HistorySession,
)

__all__ = [
    "DurableHistoryProvider",
    "HistorySession",
    "HistoryMessage",
]
