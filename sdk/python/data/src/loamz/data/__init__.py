"""Typed Python client for the Loamz Data API (sync and async).

Module-level helpers connect lazily and discover their connection, so agent code is one line:

    import loamz.data as ld
    df = ld.sql("SELECT zone, count(*) AS trips FROM main.nyc.yellow GROUP BY 1")

Inside a Loamz code-interpreter sandbox the connection is a Unix socket and a per-run token file mounted at
`/run/loamz-data`. Elsewhere set `LOAMZ_DATA_URL` and `LOAMZ_DATA_TOKEN`, or build a `DataClient` /
`AsyncDataClient` yourself.

The core depends only on httpx (and `loamz-identity` for the tenancy rules). `ld.sql`, `ld.arrow` and `ld.stream`
need the Arrow extra (`pip install 'loamz-data[arrow]'`) and say so when it is missing; `ld.rows` returns
plain JSON rows without it.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import httpx

from loamz.data._http import RetryPolicy, trace_context
from loamz.data.auth import ClientCredentials
from loamz.data.client import DEFAULT_MAX_BYTES, AsyncDataClient, DataClient
from loamz.data.errors import (
    ConflictError,
    DataError,
    ForbiddenError,
    InsufficientScopeError,
    InvalidRequestError,
    NotFoundError,
    PreconditionFailedError,
    QuotaExceededError,
    RateLimitedError,
    ResultTooLargeError,
    ScopeViolationError,
    ServiceUnavailableError,
    UnauthenticatedError,
)
from loamz.data.files import locate
from loamz.data.models import FileInfo, FileVersion, Listing, QueryResult, Usage

SANDBOX_DIR = Path("/run/loamz-data")
SANDBOX_HOST = "http://loamz-data"
_lock = threading.Lock()
_default: DataClient | None = None


def _sandbox() -> tuple[Path, Path] | None:
    socket, token_file = SANDBOX_DIR / "api.sock", SANDBOX_DIR / "token"
    return (socket, token_file) if socket.exists() else None


def _from_environment(url: str | None, token: Any) -> tuple[str, Any]:
    url = url or os.environ.get("LOAMZ_DATA_URL")
    if not url:
        raise RuntimeError(
            "No Loamz Data connection: not in a sandbox, and LOAMZ_DATA_URL is not set."
        )
    return url, token or os.environ.get("LOAMZ_DATA_TOKEN")


def connect(url: str | None = None, token: str | None = None) -> DataClient:
    """A client for this environment: the sandbox socket if present, else URL/token or environment variables."""
    sandbox = _sandbox() if url is None else None
    if sandbox:
        socket, token_file = sandbox
        # The run's token is a file the platform mounts; read it each time so a refreshed file is honoured and
        # nothing keeps it in a variable agent code might print.
        return DataClient(
            SANDBOX_HOST,
            token=lambda: token_file.read_text().strip(),
            transport=httpx.HTTPTransport(uds=str(socket)),
        )
    return DataClient(*_from_environment(url, token))


def aconnect(url: str | None = None, token: Any = None) -> AsyncDataClient:
    """`connect()` for asyncio code (same discovery)."""
    sandbox = _sandbox() if url is None else None
    if sandbox:
        socket, token_file = sandbox
        return AsyncDataClient(
            SANDBOX_HOST,
            token=lambda: token_file.read_text().strip(),
            transport=httpx.AsyncHTTPTransport(uds=str(socket)),
        )
    return AsyncDataClient(*_from_environment(url, token))


def _client() -> DataClient:
    global _default
    with _lock:
        if _default is None:
            _default = connect()
        return _default


def sql(query: str, **options: Any):
    """Run a read-only SELECT and get a Polars DataFrame. Filters and aggregation run server-side."""
    return _client().sql(query, **options)


def arrow(query: str, **options: Any):
    return _client().sql.arrow(query, **options)


def stream(query: str, **options: Any):
    """Iterate Arrow record batches for results too large to hold at once."""
    return _client().sql.stream(query, **options)


def rows(query: str, **options: Any) -> QueryResult:
    """A small result as JSON rows; works without the Arrow extra."""
    return _client().sql.rows(query, **options)


def search(text: str = "", *, limit: int = 25) -> list[dict[str, Any]]:
    """Tables and views you may read, matching a word in the name, comment or a column."""
    return _client().search(text, limit=limit)


def describe(full_name: str, *, sample: int = 5) -> dict[str, Any]:
    """Columns, row count, partitioning and a sample of `catalog.schema.table_or_view`."""
    return _client().describe(full_name, sample=sample)


def tables(catalog: str = "main", schema: str = "default") -> list[dict[str, Any]]:
    return _client().tables(catalog, schema)


__all__ = [
    "DEFAULT_MAX_BYTES",
    "AsyncDataClient",
    "ClientCredentials",
    "ConflictError",
    "DataClient",
    "DataError",
    "FileInfo",
    "FileVersion",
    "ForbiddenError",
    "InsufficientScopeError",
    "InvalidRequestError",
    "Listing",
    "NotFoundError",
    "PreconditionFailedError",
    "QueryResult",
    "QuotaExceededError",
    "RateLimitedError",
    "ResultTooLargeError",
    "RetryPolicy",
    "ScopeViolationError",
    "ServiceUnavailableError",
    "UnauthenticatedError",
    "Usage",
    "aconnect",
    "arrow",
    "connect",
    "describe",
    "locate",
    "rows",
    "search",
    "sql",
    "stream",
    "tables",
    "trace_context",
]
