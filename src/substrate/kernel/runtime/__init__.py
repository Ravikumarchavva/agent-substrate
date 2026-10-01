"""substrate.kernel.runtime — the durable engine.

A ``Runtime`` runs agents over a ``RuntimeStore``; its worker leases runs and replays
them from a journal, so a run survives a crash, a restart and days of dormancy.
"""

from __future__ import annotations

from substrate.kernel.runtime.cancellation import CancellationToken
from substrate.kernel.runtime.context import Agent, RunContext
from substrate.kernel.runtime.journal import Journal
from substrate.kernel.runtime.resolver import ActorFactory, ActorResolver
from substrate.kernel.runtime.runtime import RunOutcome, Runtime
from substrate.kernel.runtime.sqlite_store import SqliteRuntimeStore
from substrate.kernel.runtime.worker import Worker

__all__ = [
    "ActorFactory",
    "ActorResolver",
    "Agent",
    "CancellationToken",
    "Journal",
    "RunContext",
    "RunOutcome",
    "Runtime",
    "SqliteRuntimeStore",
    "Worker",
]
