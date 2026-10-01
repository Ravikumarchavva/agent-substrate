"""A runtime for tests: the real engine over a throwaway SQLite store.

There is deliberately no hand-written in-memory runtime to maintain beside the real
one. ``:memory:`` goes through exactly the same code as the on-disk store, so a test
exercises the engine a deployment runs rather than a lookalike that can drift from it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from substrate.kernel.runtime.runtime import Runtime
from substrate.kernel.runtime.sqlite_store import SqliteRuntimeStore


@asynccontextmanager
async def ephemeral_runtime(**options: Any) -> AsyncIterator[Runtime]:
    """A started ``Runtime`` on an in-memory store, stopped on exit."""
    async with Runtime(SqliteRuntimeStore(":memory:"), **options) as runtime:
        yield runtime


__all__ = ["ephemeral_runtime"]
