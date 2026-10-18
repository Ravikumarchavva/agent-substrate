"""`DataClient` (sync) and `AsyncDataClient` (asyncio): one pooled connection set per client.

    from loamz.data.client import DataClient, ClientCredentials
    client = DataClient("https://data.example.com", token=ClientCredentials(issuer, id, secret))
    client.catalog.search("taxi")
    client.sql.rows("SELECT borough, count(*) FROM main.nyc.yellow GROUP BY 1")      # JSON, no extras
    client.sql("SELECT ...")                                                         # Polars ([arrow])
    client.files.upload("main.ws.files", "reports/q3.csv", "q3.csv")

    async with AsyncDataClient(url, token=my_async_token_function) as client:
        await client.files.upload(...)

The core needs only httpx. Idempotent requests are retried on 429/502/503/504 (honouring
`Retry-After`); every request carries a W3C `traceparent`; errors are `DataError` subclasses.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Self

import httpx

from loamz.data.client._http import STORAGE_RETRY, AsyncCore, RetryPolicy, SyncCore
from loamz.data.client.auth import TokenSource
from loamz.data.client.catalog import AsyncCatalog, AsyncTenants, Catalog, Tenants
from loamz.data.client.documents import AsyncDocuments, Documents
from loamz.data.client.errors import DataError, NotFoundError
from loamz.data.client.files import AsyncFiles, Files, _part_etag, _storage_failure
from loamz.data.client.memory import AsyncMemory, Memory
from loamz.data.client.privacy import AsyncErasure, AsyncExports, Erasure, Exports
from loamz.data.client.sql import DEFAULT_MAX_BYTES, AsyncSql, Sql

API_VERSION = "v1"
DEFAULT_URL = "http://127.0.0.1:8020"
STORAGE_TIMEOUT = httpx.Timeout(30, read=600)

__all__ = ["API_VERSION", "DEFAULT_MAX_BYTES", "AsyncDataClient", "DataClient"]


def _import_request(table: str, files: list[Path], mode, partitioning, file_format) -> dict:
    return {
        "table": table,
        "mode": mode,
        "format": file_format,
        "partitioning": partitioning or [],
        "files": [{"name": p.name, "size": p.stat().st_size} for p in files],
    }


class DataClient:
    """Synchronous client. Thread-safe; share one per process."""

    def __init__(
        self,
        url: str = DEFAULT_URL,
        token: TokenSource | Any = None,
        *,
        timeout: float = 600,
        transport: httpx.BaseTransport | None = None,
        storage_transport: httpx.BaseTransport | None = None,
        retry: RetryPolicy | None = RetryPolicy(),
        http2: bool | None = None,
        traceparent: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.token = token
        self._core = SyncCore(
            base_url=url.rstrip("/") + f"/api/{API_VERSION}",
            token=token,
            timeout=timeout,
            transport=transport,
            retry=retry,
            http2=http2,
            traceparent=traceparent,
            sleep=sleep,
        )
        # Presigned URLs: another host, no bearer token, no API trace headers.
        self._storage = SyncCore(
            api=False,
            timeout=STORAGE_TIMEOUT,
            transport=storage_transport,
            retry=STORAGE_RETRY if retry else None,
            http2=http2,
            sleep=sleep,
        )
        self.catalog = Catalog(self._core)
        self.sql = Sql(self._core)
        self.files = Files(self._core, self._storage)
        self.tenants = Tenants(self._core)
        self.documents = Documents(self._core)
        self.memory = Memory(self._core)
        self.erasure = Erasure(self._core)
        self.exports = Exports(self._core)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self._core.close()
        self._storage.close()

    @property
    def http(self) -> httpx.Client:
        return self._core.http

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Any `/api/v1` call; returns the decoded JSON (None for an empty body)."""
        return self._core.call(method, path, **kwargs)

    # ------------------------------------------------------------------ queries (shortcuts)
    def stream(self, query: str, **options: Any):
        return self.sql.stream(query, **options)

    def arrow(self, query: str, **options: Any):
        return self.sql.arrow(query, **options)

    # ------------------------------------------------------------------ catalog (shortcuts)
    def catalogs(self) -> list[dict[str, Any]]:
        return self.catalog.catalogs()

    def schemas(self, catalog: str) -> list[dict[str, Any]]:
        return self.catalog.schemas(catalog)

    def tables(self, catalog: str, schema: str) -> list[dict[str, Any]]:
        return self.catalog.tables(catalog, schema)

    def table(self, full_name: str) -> dict[str, Any]:
        return self.catalog.table(full_name)

    def views(self, catalog: str, schema: str) -> list[dict[str, Any]]:
        return self.catalog.views(catalog, schema)

    def search(self, text: str = "", *, limit: int = 25) -> list[dict[str, Any]]:
        """Tables and views you may read whose name, comment or a column matches `text`."""
        return self.catalog.search(text, limit=limit)

    def create_view(self, full_name: str, sql: str, comment: str | None = None) -> dict[str, Any]:
        return self.catalog.create_view(full_name, sql, comment)

    def create_schema(self, catalog: str, name: str, comment: str | None = None) -> dict[str, Any]:
        return self.catalog.create_schema(catalog, name, comment)

    def describe(self, full_name: str, *, sample: int = 5) -> dict[str, Any]:
        """Columns, size, partitioning and a few sample rows of a table or view.

        `sample` is a Polars DataFrame with the [arrow] extra, else a `QueryResult`."""
        try:
            info = {"kind": "table", **self.catalog.table(full_name)}
        except NotFoundError:
            info = {"kind": "view", **self.catalog.view(full_name)}
        if sample:
            query = f"SELECT * FROM {full_name} LIMIT {int(sample)}"
            try:
                info["sample"] = self.sql(query)
            except ImportError:
                info["sample"] = self.sql.rows(query)
        return info

    # ------------------------------------------------------------------ imports
    def upload(
        self,
        table: str,
        files: list[str | Path],
        *,
        mode: str = "create",
        partitioning: list[dict[str, str]] | None = None,
        file_format: str | None = None,
        parallelism: int = 8,
        wait: bool = True,
        poll_seconds: float = 1.0,
    ) -> dict[str, Any]:
        """Upload local files straight to object storage, then ingest them into `table`."""
        paths = [Path(f) for f in files]
        created = self.request(
            "POST", "/imports", json=_import_request(table, paths, mode, partitioning, file_format)
        )
        completions = []
        with ThreadPoolExecutor(max_workers=parallelism) as pool:
            for path, upload in zip(paths, created["uploads"], strict=True):
                if upload["method"] == "PUT":
                    pool.submit(self._put, upload["url"], path, 0, path.stat().st_size).result()
                    completions.append({"key": upload["key"], "parts": []})
                    continue
                size = upload["part_size"]
                futures = [
                    pool.submit(
                        self._put, part["url"], path, (part["part_number"] - 1) * size, size
                    )
                    for part in upload["parts"]
                ]
                completions.append(
                    {
                        "key": upload["key"],
                        "parts": [
                            {"part_number": part["part_number"], "etag": future.result()}
                            for part, future in zip(upload["parts"], futures, strict=True)
                        ],
                    }
                )
        record = self.request(
            "POST",
            f"/imports/{created['import']['id']}:complete",
            json={"files": completions},
            idempotent=True,
        )
        while wait and record["status"] not in {"succeeded", "failed"}:
            time.sleep(poll_seconds)
            record = self.request("GET", f"/imports/{record['id']}")
        if record["status"] == "failed":
            raise DataError(409, {"type": "import-failed", "detail": record.get("error")})
        return record

    def _put(self, url: str, path: Path, offset: int, length: int) -> str:
        with path.open("rb") as stream:
            stream.seek(offset)
            data = stream.read(length)
        response = self._storage.send("PUT", url, content=data)
        if response.status_code >= 400:
            response.read()
            raise _storage_failure(response)
        return _part_etag(response)


class AsyncDataClient:
    """asyncio client over one `httpx.AsyncClient` (HTTP/2 when `h2` is installed, else HTTP/1.1)."""

    def __init__(
        self,
        url: str = DEFAULT_URL,
        token: TokenSource | Any = None,
        *,
        timeout: float = 600,
        transport: httpx.AsyncBaseTransport | None = None,
        storage_transport: httpx.AsyncBaseTransport | None = None,
        retry: RetryPolicy | None = RetryPolicy(),
        http2: bool | None = None,
        traceparent: str | None = None,
        sleep: Callable[[float], Any] | None = None,
    ):
        import asyncio

        sleep = sleep or asyncio.sleep
        self.token = token
        self._core = AsyncCore(
            base_url=url.rstrip("/") + f"/api/{API_VERSION}",
            token=token,
            timeout=timeout,
            transport=transport,
            retry=retry,
            http2=http2,
            traceparent=traceparent,
            sleep=sleep,
        )
        self._storage = AsyncCore(
            api=False,
            timeout=STORAGE_TIMEOUT,
            transport=storage_transport,
            retry=STORAGE_RETRY if retry else None,
            http2=http2,
            sleep=sleep,
        )
        self.catalog = AsyncCatalog(self._core)
        self.sql = AsyncSql(self._core)
        self.files = AsyncFiles(self._core, self._storage)
        self.tenants = AsyncTenants(self._core)
        self.documents = AsyncDocuments(self._core)
        self.memory = AsyncMemory(self._core)
        self.erasure = AsyncErasure(self._core)
        self.exports = AsyncExports(self._core)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._core.close()
        await self._storage.close()

    @property
    def http(self) -> httpx.AsyncClient:
        return self._core.http

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        return await self._core.call(method, path, **kwargs)

    def stream(self, query: str, **options: Any):
        return self.sql.stream(query, **options)

    async def arrow(self, query: str, **options: Any):
        return await self.sql.arrow(query, **options)

    async def search(self, text: str = "", *, limit: int = 25) -> list[dict[str, Any]]:
        return await self.catalog.search(text, limit=limit)

    async def describe(self, full_name: str, *, sample: int = 5) -> dict[str, Any]:
        try:
            info = {"kind": "table", **await self.catalog.table(full_name)}
        except NotFoundError:
            info = {"kind": "view", **await self.catalog.view(full_name)}
        if sample:
            query = f"SELECT * FROM {full_name} LIMIT {int(sample)}"
            try:
                info["sample"] = await self.sql(query)
            except ImportError:
                info["sample"] = await self.sql.rows(query)
        return info
