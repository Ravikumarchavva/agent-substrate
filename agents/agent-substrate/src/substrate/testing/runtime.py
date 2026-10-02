"""A runtime for tests: the real engine over a store in a throwaway folder.

There is deliberately no hand-written in-memory runtime to maintain beside the real one: a test exercises the
engine a deployment runs — the same database, the same files — in a folder that is removed afterwards.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from substrate.runtime.runtime import Runtime
from substrate.runtime.sql_store import SqlRuntimeStore
from substrate.stores import Store


@asynccontextmanager
async def ephemeral_runtime(**options: Any) -> AsyncGenerator[Runtime]:
    """A started ``Runtime`` on a store in a temporary folder, stopped and removed on exit."""
    with tempfile.TemporaryDirectory(prefix="substrate-test-") as folder:
        async with Runtime.open(folder, **options) as runtime:
            yield runtime


class _FolderRuntimeStore(SqlRuntimeStore):
    """A runtime store that owns the folder store it runs on, for tests that use a store without a ``Runtime``."""

    def __init__(self, folder: str | Path | None, **options: Any) -> None:
        self._temporary = folder is None
        self._folder = Path(tempfile.mkdtemp(prefix="substrate-test-") if folder is None else folder)
        self._owner = Store.at(self._folder)
        super().__init__(self._owner.database, **options)

    async def start(self) -> None:
        await self._owner.start()
        await super().start()

    async def aclose(self) -> None:
        await self._owner.aclose()
        if self._temporary:
            shutil.rmtree(self._folder, ignore_errors=True)


def runtime_store(folder: str | Path | None = None, **options: Any) -> SqlRuntimeStore:
    """A runtime store on a store in ``folder`` (a temporary one, removed on close, when none is given).
    ``start()`` opens it and ``aclose()`` closes it."""
    return _FolderRuntimeStore(folder, **options)


__all__ = ["ephemeral_runtime", "runtime_store"]
