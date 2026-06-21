"""substrate.agents.memory — in-process memory implementations.

These have no external dependencies and are suitable for tests, notebooks,
and single-process deployments.  Production deployments use the adapters
in ``substrate.capabilities.memory`` (Redis, Postgres).
"""

from __future__ import annotations

from substrate.agents.memory.session_store import InMemorySessionStore

__all__ = ["InMemorySessionStore"]
