"""PostgresQueryConnector — run read-only SQL queries against a Postgres database.

Uses asyncpg with read-only transaction mode by default for safety.
Marked as SENSITIVE risk because it reads potentially sensitive data.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class PostgresQueryConnector:
    """Async Postgres connector for read-only SQL queries.

    Parameters
    ----------
    dsn
        PostgreSQL connection string (e.g.  ``postgresql://user:pass@host/db``).
    read_only
        If True (default), executes queries inside a read-only transaction.
    max_rows
        Maximum rows to return per query.
    """

    def __init__(
        self,
        dsn: str = "",
        read_only: bool = True,
        max_rows: int = 1000,
    ) -> None:
        self._dsn = dsn
        self._read_only = read_only
        self._max_rows = max_rows
        self._pool: Optional[Any] = None

    async def connect(self) -> None:
        """Create connection pool."""
        if not self._dsn:
            raise RuntimeError("Postgres DSN not configured")
        import asyncpg

        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)

    async def disconnect(self) -> None:
        """Close connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def query(
        self,
        sql: str,
        *,
        params: Optional[List[Any]] = None,
        max_rows: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Execute a SQL query and return results as list of dicts.

        Parameters
        ----------
        sql
            SQL query string.  Parameterised with ``$1``, ``$2``, etc.
        params
            Positional parameters for the query.
        max_rows
            Override instance max_rows for this query.
        """
        assert self._pool is not None, "Not connected"
        limit = max_rows if max_rows is not None else self._max_rows

        async with self._pool.acquire() as conn:
            if self._read_only:
                async with conn.transaction(readonly=True):
                    rows = await conn.fetch(sql, *(params or []))
            else:
                rows = await conn.fetch(sql, *(params or []))

        results = [dict(row) for row in rows[:limit]]
        logger.debug("Query returned %d rows (limit %d)", len(results), limit)
        return results

    async def list_tables(self) -> List[str]:
        """List all user tables in the public schema."""
        rows = await self.query(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
        )
        return [row["tablename"] for row in rows]

    async def describe_table(self, table_name: str) -> List[Dict[str, Any]]:
        """Return column info for a table."""
        return await self.query(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = $1 "
            "ORDER BY ordinal_position",
            params=[table_name],
        )
