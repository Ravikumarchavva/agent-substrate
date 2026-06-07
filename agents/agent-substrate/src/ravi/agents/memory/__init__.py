"""ravi.agents.memory — in-process memory implementations.

These have no external dependencies and are suitable for tests, notebooks,
and single-process deployments.  Production deployments use the adapters
in ``ravi.adapters.memory`` (Redis, Postgres).
"""

from __future__ import annotations

from ravi.agents.memory.session_store import InMemorySessionStore

__all__ = ["InMemorySessionStore"]
