"""SQL written once, run on SQLite (one machine, zero setup) or PostgreSQL (many machines).

    db = sqlite("./state.db")          # or: postgres("postgresql://…", schema="tenant_acme")
    await migrate(db, "threads", THREAD_SCRIPTS)
    async with db.transaction() as tx:
        await tx.execute("INSERT INTO …", …)

Every component owns its tables as append-only migration scripts (`migrate`), recorded in the database itself,
so state written by a newer build is refused rather than misread.
"""

from loamz.data.storage.sql.database import (
    Database,
    Row,
    SchemaVersionError,
    Script,
    Tx,
    migrate,
    under,
)
from loamz.data.storage.sql.sqlite import SqliteDatabase


def sqlite(path: str = ":memory:") -> SqliteDatabase:
    """An embedded SQLite database (a file path, or ``:memory:``)."""
    return SqliteDatabase(path)


def postgres(
    dsn: str, *, schema: str | None = None, pool_min_size: int = 2, pool_max_size: int = 10
):
    """A PostgreSQL database over an asyncpg pool (the ``postgres`` extra)."""
    from loamz.data.storage.sql.postgres import PostgresDatabase

    return PostgresDatabase(
        dsn, schema=schema, pool_min_size=pool_min_size, pool_max_size=pool_max_size
    )


__all__ = [
    "Database",
    "Row",
    "SchemaVersionError",
    "Script",
    "SqliteDatabase",
    "Tx",
    "migrate",
    "postgres",
    "sqlite",
    "under",
]
