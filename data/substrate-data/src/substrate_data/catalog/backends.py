"""Where the control-plane catalog lives: one SQLite file (development, single node) or PostgreSQL
(production, several API replicas). `CatalogStore` is written once, in SQL both understand; this
module owns the differences: connections and transactions, placeholder syntax, case-insensitive
matching, 64-bit counters and full-text search.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

LOOPBACK = {"localhost", "127.0.0.1", "::1"}
ENCRYPTED = {"require", "verify-ca", "verify-full"}
SCHEMA_LOCK = 7_291_001  # serializes schema setup when several replicas start together


def check_url(url: str) -> str:
    """A PostgreSQL connection string that encrypts anything leaving this machine."""
    parts = conninfo_to_dict(url)
    host = parts.get("host", "localhost")
    if host.startswith("/"):  # a Unix socket directory
        return url
    if host not in LOOPBACK and parts.get("sslmode", "prefer") not in ENCRYPTED:
        raise ValueError(
            "The catalog database is on another machine: set sslmode=verify-full "
            "(or verify-ca, require) in SUBSTRATE_DATA_METADATA_URL"
        )
    return url


# ------------------------------------------------------------------ SQL dialect translation
def to_postgres(sql: str) -> str:
    """The store's SQL (SQLite flavour: `?` placeholders) as PostgreSQL."""
    out: list[str] = []
    quoted = False
    for char in sql:
        if char == "'":
            quoted = not quoted
        if not quoted and char == "?":
            out.append("%s")
        elif char == "%":
            out.append("%%")
        else:
            out.append(char)
    text = "".join(out)
    text = re.sub(r"\bLIKE\b", "ILIKE", text)  # SQLite's LIKE ignores case; PostgreSQL's does not
    if re.match(r"\s*INSERT OR IGNORE INTO", text):
        text = re.sub(r"INSERT OR IGNORE INTO", "INSERT INTO", text, count=1)
        text += " ON CONFLICT DO NOTHING"
    return text


def postgres_schema(sqlite_schema: str) -> list[str]:
    """DDL statements for PostgreSQL derived from the SQLite schema: 64-bit integers (an
    `INTEGER` here would overflow at two billion rows), no pragmas, native full-text search."""
    statements = []
    for raw in sqlite_schema.split(";"):
        statement = raw.strip()
        if not statement or statement.startswith("PRAGMA"):
            continue
        statement = statement.replace(
            "INTEGER PRIMARY KEY AUTOINCREMENT", "BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY"
        )
        statement = re.sub(r"\bINTEGER\b", "BIGINT", statement)
        statement = re.sub(r"\bREAL\b", "DOUBLE PRECISION", statement)
        statements.append(statement)
    return statements


class Leader:
    """Whether this process should run work only one replica may do (publishing finished jobs)."""

    def acquire(self) -> bool:
        return True

    def release(self) -> None:
        pass


# ------------------------------------------------------------------ SQLite
class SQLiteBackend:
    dialect = "sqlite"
    integrity_errors: tuple[type[Exception], ...] = (sqlite3.IntegrityError,)

    def __init__(self, path: Path, schema: str, migrations: tuple[tuple[str, str, str], ...]):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.local = threading.local()
        # Schema DDL and the WAL pragma cannot run inside the explicit transactions used below.
        setup = sqlite3.connect(path, timeout=10)
        try:
            setup.executescript(schema)
            for table, column, definition in migrations:
                present = {r[1] for r in setup.execute(f"PRAGMA table_info({table})")}
                if column not in present:
                    setup.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                    setup.commit()
        finally:
            setup.close()
        path.chmod(0o600)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """One pooled connection per thread; each `with` block is one transaction."""
        db = getattr(self.local, "db", None)
        if db is None:
            db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=10000")
            # WAL + NORMAL: commits survive process crashes; no fsync per transaction.
            db.execute("PRAGMA synchronous=NORMAL")
            self.local.db = db
        if db.in_transaction:  # nested use within one transaction
            yield db
            return
        db.execute("BEGIN IMMEDIATE")
        try:
            yield db
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise

    def lock_audit(self, db: Any) -> None:
        pass  # BEGIN IMMEDIATE already serializes writers

    def leader(self, name: str) -> Leader:
        return Leader()  # one file, one process

    def close(self) -> None:
        pass


# ------------------------------------------------------------------ PostgreSQL
class PgConnection:
    """A PostgreSQL connection that accepts the store's SQL as written."""

    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def execute(self, sql: str, params: Any = ()) -> psycopg.Cursor:
        return self.conn.execute(to_postgres(sql), params)

    def executemany(self, sql: str, rows: Any) -> None:
        with self.conn.cursor() as cursor:
            cursor.executemany(to_postgres(sql), rows)


class PostgresLeader(Leader):
    """A session-level advisory lock on a connection of its own: held while this process lives,
    released by the server the moment the connection drops, so another replica takes over."""

    def __init__(self, url: str, name: str):
        self.url, self.key = url, SCHEMA_LOCK + 1 + (sum(map(ord, name)) % 1000)
        self.conn: psycopg.Connection | None = None
        self.held = False

    def acquire(self) -> bool:
        try:
            if self.conn is None or self.conn.closed:
                self.conn, self.held = psycopg.connect(self.url, autocommit=True), False
            if self.held:
                self.conn.execute("SELECT 1")  # raises if the connection (and so the lock) is gone
                return True
            row = self.conn.execute("SELECT pg_try_advisory_lock(%s)", (self.key,)).fetchone()
            self.held = bool(row and row[0])
            return self.held
        except psycopg.Error:
            self.held = False
            if self.conn is not None:
                self.conn.close()
            self.conn = None
            return False

    def release(self) -> None:
        if self.conn is not None:
            self.conn.close()
        self.conn, self.held = None, False


class PostgresBackend:
    dialect = "postgres"
    integrity_errors: tuple[type[Exception], ...] = (psycopg.errors.IntegrityError,)

    def __init__(
        self,
        url: str,
        schema: str,
        migrations: tuple[tuple[str, str, str], ...],
        pool_size: int = 16,
    ):
        self.url = check_url(url)
        self.local = threading.local()
        self.pool = ConnectionPool(
            self.url,
            min_size=1,
            max_size=pool_size,
            kwargs={"autocommit": True, "row_factory": dict_row},
            open=True,
            timeout=15,
        )
        with psycopg.connect(self.url, autocommit=True) as setup:
            setup.execute("SELECT pg_advisory_lock(%s)", (SCHEMA_LOCK,))
            try:
                for statement in postgres_schema(schema):
                    setup.execute(statement)
                for table, column, definition in migrations:
                    setup.execute(
                        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} "
                        + re.sub(r"\bINTEGER\b", "BIGINT", definition)
                    )
            finally:
                setup.execute("SELECT pg_advisory_unlock(%s)", (SCHEMA_LOCK,))

    @contextmanager
    def connection(self) -> Iterator[PgConnection]:
        current = getattr(self.local, "current", None)
        if current is not None:  # nested use within one transaction
            yield current
            return
        with self.pool.connection() as conn, conn.transaction():
            self.local.current = PgConnection(conn)
            try:
                yield self.local.current
            finally:
                self.local.current = None

    def lock_audit(self, db: Any) -> None:
        # Held until the transaction ends; readers are not blocked.
        db.conn.execute("LOCK TABLE audit_events IN SHARE ROW EXCLUSIVE MODE")

    def leader(self, name: str) -> Leader:
        return PostgresLeader(self.url, name)

    def close(self) -> None:
        self.pool.close()
