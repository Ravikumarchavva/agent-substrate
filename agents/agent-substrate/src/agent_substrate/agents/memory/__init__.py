"""agent_substrate.agents.memory — in-process memory implementations.

These have no external dependencies and are suitable for tests, notebooks,
and single-process deployments.  Production deployments use the adapters
in ``agent_substrate.capabilities.memory`` (Redis, Postgres).
"""

from __future__ import annotations

from agent_substrate.agents.memory.session_store import InMemorySessionStore

__all__ = ["InMemorySessionStore"]
