"""Client for Loamz Data: catalog, SQL, files, documents, memory and managed databases (sync and async).

    from loamz.data import DataClient
    from loamz.identity import DefaultLoamzCredential

    client = DataClient("https://data.example.com", DefaultLoamzCredential())
    df = client.sql("SELECT zone, count(*) AS trips FROM main.nyc.yellow GROUP BY 1")

    from loamz.data.aio import DataClient          # the same client for asyncio

Module-level helpers connect lazily and discover their connection, so agent code is one line:

    import loamz.data as ld
    df = ld.sql("SELECT ...")

Inside a Loamz code-interpreter sandbox the connection is a Unix socket and a per-run token file mounted at
`/run/loamz-data`. Elsewhere set `LOAMZ_ENDPOINT` and a credential (`LOAMZ_TOKEN`, or `LOAMZ_CLIENT_ID`,
`LOAMZ_CLIENT_SECRET` and `LOAMZ_ISSUER`; see `loamz.identity`), or keep them in a `~/.loamz/config` profile.

The core depends only on httpx. `ld.sql`, `ld.arrow` and `ld.stream` need the Arrow extra
(`pip install 'loamz-data[arrow]'`) and say so when it is missing; `ld.rows` returns plain JSON rows without it.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import httpx

from loamz.core.credentials import TokenSource
from loamz.data.client import DEFAULT_MAX_BYTES, AsyncDataClient, DataClient
from loamz.data.models import FileInfo, FileVersion, Listing, QueryResult, Usage
from loamz.identity import DefaultLoamzCredential, default_endpoint

SANDBOX_DIR = Path("/run/loamz-data")
SANDBOX_HOST = "http://loamz-data"
_lock = threading.Lock()
_default: DataClient | None = None


def _sandbox() -> tuple[Path, Path] | None:
    socket, token_file = SANDBOX_DIR / "api.sock", SANDBOX_DIR / "token"
    return (socket, token_file) if socket.exists() else None


def _from_environment(endpoint: str | None, credential: TokenSource) -> tuple[str, TokenSource]:
    endpoint = endpoint or default_endpoint()
    if not endpoint:
        raise RuntimeError(
            "No Loamz Data connection: not in a sandbox, and LOAMZ_ENDPOINT (or a ~/.loamz/config profile) is not set."
        )
    return endpoint, credential or DefaultLoamzCredential()


def connect(endpoint: str | None = None, credential: TokenSource = None) -> DataClient:
    """A client for this environment: the sandbox socket if present, else the endpoint and credential given or found."""
    sandbox = _sandbox() if endpoint is None else None
    if sandbox:
        socket, token_file = sandbox
        # The run's token is a file the platform mounts; read it each time so a refreshed file is honoured and
        # nothing keeps it in a variable agent code might print.
        return DataClient(
            SANDBOX_HOST,
            lambda: token_file.read_text().strip(),
            transport=httpx.HTTPTransport(uds=str(socket)),
        )
    return DataClient(*_from_environment(endpoint, credential))


def aconnect(endpoint: str | None = None, credential: TokenSource = None) -> AsyncDataClient:
    """`connect()` for asyncio code (same discovery)."""
    sandbox = _sandbox() if endpoint is None else None
    if sandbox:
        socket, token_file = sandbox
        return AsyncDataClient(
            SANDBOX_HOST,
            lambda: token_file.read_text().strip(),
            transport=httpx.AsyncHTTPTransport(uds=str(socket)),
        )
    return AsyncDataClient(*_from_environment(endpoint, credential))


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
    "DataClient",
    "FileInfo",
    "FileVersion",
    "Listing",
    "QueryResult",
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
]
