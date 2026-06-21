"""PostgresConnector — asyncpg connection pool for the engine's own database."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from substrate.logger import setup_logging

if TYPE_CHECKING:
    import asyncpg

logger = setup_logging()


class PostgresConnector:
    """Async PostgreSQL connector backed by an asyncpg connection pool.

    This is the infrastructure connector for the engine's own Postgres instance
    (agent runtime tables, history, vector store, etc.), not for querying
    arbitrary user databases (see capabilities/tools/database/ for that).

    Parameters
    ----------
    url
        PostgreSQL connection string (e.g. ``postgresql://user:pass@host/db``).
    min_size / max_size
        Pool size bounds.
    """

    def __init__(
        self,
        url: str = "",
        *,
        min_size: int = 2,
        max_size: int = 10,
    ) -> None:
        self._url = url
        self._min_size = min_size
        self._max_size = max_size
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        """Create the asyncpg connection pool."""
        if not self._url:
            raise RuntimeError("Postgres URL not configured")
        import asyncpg as _asyncpg

        self._pool = await _asyncpg.create_pool(
            self._url,
            min_size=self._min_size,
            max_size=self._max_size,
        )

    async def disconnect(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @property
    def pool(self) -> asyncpg.Pool:
        """The underlying asyncpg pool. Available after connect()."""
        if self._pool is None:
            raise RuntimeError("PostgresConnector not connected")
        return self._pool

    async def execute(self, query: str, *args: Any) -> str:
        """Execute a statement (INSERT, UPDATE, DDL, etc.)."""
        async with self.pool.acquire() as conn:
            return await conn.execute(query, *args)

    async def fetch(self, query: str, *args: Any) -> list[Any]:
        """Run a SELECT and return all rows as a list of Record objects."""
        async with self.pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args: Any) -> Any | None:
        """Run a SELECT and return the first row, or None."""
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def fetchval(self, query: str, *args: Any) -> Any:
        """Run a SELECT and return a single scalar value."""
        async with self.pool.acquire() as conn:
            return await conn.fetchval(query, *args)
