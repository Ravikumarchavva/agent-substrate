"""Substrate Data client.

Module-level helpers connect lazily and discover their connection, so agent code is one line:

    import substrate_data_client as sd
    df = sd.sql("SELECT zone, count(*) AS trips FROM main.nyc.yellow GROUP BY 1")

Inside a Substrate code-interpreter sandbox the connection is a Unix socket and a per-run token file
mounted at `/run/substrate-data`. Elsewhere set `SUBSTRATE_DATA_URL` and `SUBSTRATE_DATA_TOKEN`, or build
a `DataClient` / `AsyncDataClient` yourself.

The core depends only on httpx. `sd.sql`, `sd.arrow` and `sd.stream` need the Arrow extra
(`pip install 'substrate-data-client[arrow]'`, which the sandbox image installs) and say so when it is
missing; `sd.rows` returns plain JSON rows without it.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import httpx

from substrate_data_client._http import RetryPolicy, trace_context
from substrate_data_client.auth import ClientCredentials
from substrate_data_client.client import DEFAULT_MAX_BYTES, AsyncDataClient, DataClient
from substrate_data_client.errors import (
    Conflict,
    DataError,
    Forbidden,
    InsufficientScope,
    InvalidRequest,
    NotFound,
    PayloadTooLarge,
    PreconditionFailed,
    QuotaExceeded,
    RateLimited,
    ResultTooLarge,
    Unauthorized,
    Unavailable,
)
from substrate_data_client.models import (
    FileInfo,
    FileVersion,
    Listing,
    QueryResult,
    Usage,
)

__version__ = "0.2.0"
SANDBOX_DIR = Path(os.environ.get("SUBSTRATE_DATA_SANDBOX_DIR", "/run/substrate-data"))
_lock = threading.Lock()
_default: DataClient | None = None


def connect(url: str | None = None, token: str | None = None) -> DataClient:
    """A client for this environment: the sandbox socket if present, else URL/token or env vars."""
    socket, token_file = SANDBOX_DIR / "api.sock", SANDBOX_DIR / "token"
    if url is None and socket.exists():

        def sandbox_token() -> str:
            # The run's token is a file the platform mounts; read it each time so a refreshed
            # file is honoured and nothing keeps it in a variable agent code might print.
            return token_file.read_text().strip()

        return DataClient(
            "http://substrate-data",
            token=sandbox_token,
            transport=httpx.HTTPTransport(uds=str(socket)),
        )
    url = url or os.environ.get("SUBSTRATE_DATA_URL")
    if not url:
        raise RuntimeError(
            "No Substrate Data connection: not in a sandbox, and SUBSTRATE_DATA_URL is not set."
        )
    return DataClient(url, token=token or os.environ.get("SUBSTRATE_DATA_TOKEN"))


def aconnect(url: str | None = None, token: Any = None) -> AsyncDataClient:
    """`connect()` for asyncio code (same discovery: sandbox socket, else URL/token or env vars)."""
    socket, token_file = SANDBOX_DIR / "api.sock", SANDBOX_DIR / "token"
    if url is None and socket.exists():
        return AsyncDataClient(
            "http://substrate-data",
            token=lambda: token_file.read_text().strip(),
            transport=httpx.AsyncHTTPTransport(uds=str(socket)),
        )
    url = url or os.environ.get("SUBSTRATE_DATA_URL")
    if not url:
        raise RuntimeError(
            "No Substrate Data connection: not in a sandbox, and SUBSTRATE_DATA_URL is not set."
        )
    return AsyncDataClient(url, token=token or os.environ.get("SUBSTRATE_DATA_TOKEN"))


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
    "Conflict",
    "DataClient",
    "DataError",
    "FileInfo",
    "FileVersion",
    "Forbidden",
    "InsufficientScope",
    "InvalidRequest",
    "Listing",
    "NotFound",
    "PayloadTooLarge",
    "PreconditionFailed",
    "QueryResult",
    "QuotaExceeded",
    "RateLimited",
    "ResultTooLarge",
    "RetryPolicy",
    "Unauthorized",
    "Unavailable",
    "Usage",
    "aconnect",
    "arrow",
    "connect",
    "describe",
    "rows",
    "search",
    "sql",
    "stream",
    "tables",
    "trace_context",
]
