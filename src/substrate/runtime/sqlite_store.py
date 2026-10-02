"""SqliteRuntimeStore — the default runtime store: one SQLite file, stdlib only."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from substrate.kernel.runtime.sql_store import SqlRuntimeStore
from substrate.kernel.runtime.sqlite_db import SqliteDatabase


class SqliteRuntimeStore(SqlRuntimeStore):
    """``SqlRuntimeStore`` over a SQLite file (or ``":memory:"``)."""

    def __init__(self, path: str | Path = "./data/db/runtime.sqlite3", **options: Any) -> None:
        super().__init__(SqliteDatabase(path), **options)


__all__ = ["SqliteRuntimeStore"]
