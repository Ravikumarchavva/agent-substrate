"""Throw-away agent stores for tests: each is a fresh SQLite file under one temp root, removed at exit."""

from __future__ import annotations

import tempfile
from pathlib import Path

from loamz.agents.stores import AgentStore
from loamz.agents.stores.tasks import Tasks
from loamz.agents.stores.threads import Threads
from loamz.agents.testing.doubles import MemoryFileStore

_ROOT = tempfile.TemporaryDirectory(prefix="loamz-tests-")


def folder() -> str:
    return tempfile.mkdtemp(dir=_ROOT.name)


def agent_store() -> AgentStore:
    return AgentStore.at(Path(folder()) / "agents.db", files=MemoryFileStore())


def fs_history() -> Threads:
    return agent_store().threads


def fs_tasks() -> Tasks:
    return agent_store().tasks


def agent_store_at(location: str | Path) -> AgentStore:
    """The agent store in the folder ``location`` (its ``agents.db``), with an in-memory file store."""
    return AgentStore.at(Path(location) / "agents.db", files=MemoryFileStore())


def open_runtime(location: str | Path | None = None, **options):
    """A ``Runtime`` that owns an agent store in ``location`` (a fresh temp folder when omitted)."""
    from loamz.agents.kernel.runtime import Runtime

    return Runtime(agent_store_at(location or folder()), owns_store=True, **options)
