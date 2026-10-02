"""The relational component: what the engine's state is stored in.

Everything the engine remembers — runs and their journal, threads, memory, tasks, the graph — lives in one
relational database, so that one turn can change all of it in one transaction and a crash leaves it either
all done or not at all. A ``Database`` supplies connections, transactions and two small hooks; the rules
live in the code above it, written once in SQL both SQLite and PostgreSQL accept:

* ``?`` placeholders (an adapter rewrites them), epoch-second ``DOUBLE PRECISION`` times,
  ``INSERT .. ON CONFLICT DO NOTHING`` for idempotent inserts, no database-specific functions.
* ``lock(key)`` on a transaction serialises it with every other that locks the same key — a no-op on SQLite,
  which has one writer, an advisory lock on PostgreSQL.

Each part of the engine owns its tables and ships them as ordered migrations (``migrate``), recorded in the
database itself, so a folder written by a newer build is refused rather than misread.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

Row = Mapping[str, Any]


def under(column: str, name: str) -> tuple[str, list[Any]]:
    """SQL for the rows whose ``column`` is ``name`` or lies below it (starts with ``name/``) — a name is a directory,
    as in ``FileStore.delete_prefix``. A prefix test by ``substr`` rather than ``LIKE``, so ``%`` and ``_`` in a
    tenant's id are just characters."""
    stem = name.rstrip("/") or name
    return f"({column} = ? OR substr({column}, 1, ?) = ?)", [stem, len(stem) + 1, stem + "/"]


class Tx(Protocol):
    """One open transaction."""

    async def execute(self, sql: str, *params: Any) -> int: ...
    async def fetchone(self, sql: str, *params: Any) -> Row | None: ...
    async def fetchall(self, sql: str, *params: Any) -> list[Row]: ...
    async def lock(self, key: str) -> None:
        """Serialise with every other transaction that locks ``key``, until this one ends."""
        ...


class Database(Protocol):
    """What the stores need from a database."""

    dialect: str
    """``"sqlite"`` or ``"postgresql"`` — only for the few things SQL has no common spelling for (full-text search, the
    vector column); everything else is written once."""

    auto_pk: str
    """DDL for an auto-incrementing integer primary key column."""

    async def start(self) -> None:
        """Open it. Idempotent."""
        ...

    async def aclose(self) -> None: ...
    async def script(self, ddl: str) -> None: ...
    def transaction(self) -> AbstractAsyncContextManager[Tx]: ...
    async def reclaim(self) -> None:
        """Make what was deleted unrecoverable from the database's own files, not merely invisible: after this, a
        deleted row's bytes are no longer in the file, a write-ahead log or a free page. Slow, so only for erasure."""
        ...

    def is_unique_violation(self, exc: BaseException) -> bool: ...
    def is_retryable(self, exc: BaseException) -> bool: ...


class StoreVersionError(RuntimeError):
    """The database was written by a newer build than this one understands."""


_MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS substrate_migrations (
    component TEXT NOT NULL,
    version INTEGER NOT NULL,
    applied_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (component, version)
);
"""


Script = str | Callable[["Database"], str]
"""A migration: SQL, or a function of the database that returns it, for a part whose DDL differs by dialect."""


async def migrate(database: Database, component: str, scripts: Sequence[Script]) -> None:
    """Bring ``component``'s tables up to ``len(scripts)``.

    ``scripts[i]`` is version ``i + 1``. A script may use ``{pk}`` for the database's auto-increment column
    and must be idempotent (``IF NOT EXISTS``): it runs, then the version is recorded, and a crash between
    the two — or two processes starting together — just runs it again. Scripts are never edited once
    shipped; a change is a new script.
    """
    await database.start()
    await database.script(_MIGRATIONS_TABLE)
    async with database.transaction() as tx:
        rows = await tx.fetchall("SELECT version FROM substrate_migrations WHERE component = ?", component)
    applied = max((int(row["version"]) for row in rows), default=0)
    if applied > len(scripts):
        raise StoreVersionError(
            f"{component} is at version {applied} in this database, but this build of substrate only "
            f"understands up to {len(scripts)}; it was written by a newer version"
        )
    for version in range(applied + 1, len(scripts) + 1):
        script = scripts[version - 1]
        ddl = script(database) if callable(script) else script
        await database.script(ddl.replace("{pk}", database.auto_pk))
        async with database.transaction() as tx:
            await tx.execute(
                "INSERT INTO substrate_migrations (component, version, applied_at) VALUES (?, ?, ?) "
                "ON CONFLICT DO NOTHING",
                component,
                version,
                time.time(),
            )


__all__ = ["Database", "Row", "Script", "StoreVersionError", "Tx", "migrate"]
