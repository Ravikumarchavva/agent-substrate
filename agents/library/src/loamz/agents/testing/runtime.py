"""A runtime for tests: the real engine over an agent store in a throwaway SQLite file.

There is deliberately no hand-written in-memory runtime to maintain beside the real one: a test exercises the
engine a deployment runs, on the same SQL, in a folder that is removed afterwards.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from loamz.agents.kernel.runtime.persistence.store import RuntimeStore
from loamz.agents.kernel.runtime.runtime import Runtime
from loamz.agents.stores.store import AgentStore


@asynccontextmanager
async def ephemeral_runtime(**options: Any) -> AsyncGenerator[Runtime]:
    """A started ``Runtime`` on a store in a temporary folder, stopped and removed on exit."""
    with tempfile.TemporaryDirectory(prefix="loamz-test-") as folder:
        store = AgentStore.at(Path(folder) / "agents.db")
        async with Runtime(store, owns_store=True, **options) as runtime:
            yield runtime


class _FolderRuntimeStore(RuntimeStore):
    """A runtime store that owns the agent store it runs on, for tests that use a store without a ``Runtime``."""

    def __init__(self, folder: str | Path | None, **options: Any) -> None:
        self._temporary = folder is None
        self._folder = Path(tempfile.mkdtemp(prefix="loamz-test-") if folder is None else folder)
        self._owner = AgentStore.at(self._folder / "agents.db")
        super().__init__(self._owner.database, **options)

    async def start(self) -> None:
        await self._owner.start()
        await super().start()

    async def aclose(self) -> None:
        await self._owner.aclose()
        if self._temporary:
            shutil.rmtree(self._folder, ignore_errors=True)


def runtime_store(folder: str | Path | None = None, **options: Any) -> RuntimeStore:
    """A runtime store on an agent store in ``folder`` (a temporary one, removed on close, when none is given).
    ``start()`` opens it and ``aclose()`` closes it."""
    return _FolderRuntimeStore(folder, **options)


__all__ = ["ephemeral_runtime", "runtime_store"]
