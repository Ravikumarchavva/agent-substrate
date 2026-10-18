"""Python client for Substrate Data (`/api/v1`).

    from substrate_data_client import DataClient, ClientCredentials
    client = DataClient("https://data.example.com", token=ClientCredentials(issuer, id, secret))
    df = client.sql("SELECT borough, count(*) FROM main.nyc.yellow GROUP BY 1")   # polars

Results stream as Arrow record batches and become Polars/Arrow without copying through JSON.
Uploads go straight to object storage with presigned URLs; the API never carries file bytes.
"""

from __future__ import annotations

import io
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Self

import httpx
import polars as pl
import pyarrow as pa

ARROW = "application/vnd.apache.arrow.stream"
API_VERSION = "v1"
# Default ceiling for one `sql()`/`arrow()` result held in memory. Sandboxes are small: ask SQL for
# the answer (aggregate, filter, LIMIT) or iterate `stream()` instead of pulling rows into Python.
DEFAULT_MAX_BYTES = 512 * 1024 * 1024


class DataError(Exception):
    """An RFC 9457 problem returned by the service."""

    def __init__(self, status: int, problem: dict[str, Any]):
        self.status = status
        self.problem = problem
        self.code = str(problem.get("type", "")).rsplit("/", 1)[-1]
        super().__init__(f"{status} {self.code}: {problem.get('detail', '')}")


class ResultTooLarge(DataError):
    """The result is bigger than this process should hold. Aggregate in SQL, or use `stream()`."""

    def __init__(self, limit: int):
        super().__init__(
            413,
            {
                "type": "result-too-large",
                "detail": (
                    f"The result exceeds {limit / 2**20:.0f} MiB. Aggregate or filter in SQL "
                    "(GROUP BY, WHERE, LIMIT), iterate stream() in batches, or raise max_bytes."
                ),
            },
        )


class ClientCredentials:
    """OAuth 2.0 client-credentials token source with caching until shortly before expiry."""

    def __init__(self, issuer: str, client_id: str, client_secret: str, scope: str | None = None):
        self.url = issuer.rstrip("/") + "/protocol/openid-connect/token"
        self.form = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
        if scope:
            self.form["scope"] = scope
        self.lock = threading.Lock()
        self.value: tuple[str, float] | None = None

    def __call__(self) -> str:
        with self.lock:
            if self.value and time.time() < self.value[1] - 30:
                return self.value[0]
            response = httpx.post(self.url, data=self.form, timeout=10)
            response.raise_for_status()
            body = response.json()
            self.value = (body["access_token"], time.time() + int(body.get("expires_in", 60)))
            return self.value[0]


class _Reader(io.RawIOBase):
    """Adapts an HTTP byte iterator to the file interface Arrow's stream reader expects."""

    def __init__(self, chunks: Iterator[bytes]):
        self.chunks, self.buffer = chunks, b""

    def readable(self) -> bool:
        return True

    def readinto(self, target: Any) -> int:
        while not self.buffer:
            try:
                self.buffer = next(self.chunks)
            except StopIteration:
                return 0
        size = min(len(target), len(self.buffer))
        target[:size], self.buffer = self.buffer[:size], self.buffer[size:]
        return size


class DataClient:
    def __init__(
        self,
        url: str = "http://127.0.0.1:8020",
        token: str | Callable[[], str] | None = None,
        *,
        timeout: float = 600,
        transport: httpx.BaseTransport | None = None,
    ):
        self.token = token
        self.http = httpx.Client(
            base_url=url.rstrip("/") + f"/api/{API_VERSION}",
            timeout=httpx.Timeout(30, read=timeout),
            transport=transport,
        )
        self.uploads = httpx.Client(timeout=httpx.Timeout(30, read=600))

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self.http.close()
        self.uploads.close()

    # ------------------------------------------------------------------ transport
    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = dict(extra or {})
        if self.token:
            headers["Authorization"] = "Bearer " + (
                self.token() if callable(self.token) else self.token
            )
        return headers

    def _check(self, response: httpx.Response) -> httpx.Response:
        if response.status_code >= 400:
            response.read()
            try:
                problem = response.json()
            except ValueError:
                problem = {"detail": response.text[:500]}
            raise DataError(response.status_code, problem)
        return response

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._check(
            self.http.request(
                method, path, headers=self._headers(kwargs.pop("headers", None)), **kwargs
            )
        )
        return response.json() if response.content else None

    # ------------------------------------------------------------------ queries
    def stream(
        self,
        sql: str,
        *,
        catalog: str = "main",
        schema: str = "default",
        max_rows: int | None = None,
        compute_id: str | None = None,
    ) -> Iterator[pa.RecordBatch]:
        """Record batches as the worker produces them; memory stays bounded."""
        body = {
            "sql": sql,
            "catalog": catalog,
            "schema": schema,
            "max_rows": max_rows,
            "compute_id": compute_id,
        }
        with self.http.stream(
            "POST",
            "/statements",
            json={k: v for k, v in body.items() if v is not None},
            headers=self._headers({"Accept": ARROW}),
        ) as response:
            self._check(response)
            # Arrow wants full reads; a raw adapter returns whatever chunk it holds. Buffer it.
            source = io.BufferedReader(_Reader(response.iter_raw()), buffer_size=1 << 20)
            reader = pa.ipc.open_stream(pa.PythonFile(source, mode="r"))
            yield from reader

    def arrow(
        self, sql: str, *, max_bytes: int | None = DEFAULT_MAX_BYTES, **options: Any
    ) -> pa.Table:
        """The whole result as one Arrow table, refusing to hold more than `max_bytes`."""
        batches: list[pa.RecordBatch] = []
        size = 0
        for batch in self.stream(sql, **options):
            size += batch.nbytes
            if max_bytes is not None and size > max_bytes:
                raise ResultTooLarge(max_bytes)
            batches.append(batch)
        return pa.Table.from_batches(batches) if batches else pa.table({})

    def sql(self, sql: str, **options: Any) -> pl.DataFrame:
        return pl.from_arrow(self.arrow(sql, **options))

    # ------------------------------------------------------------------ catalog
    def catalogs(self) -> list[dict[str, Any]]:
        return self.request("GET", "/catalogs")["catalogs"]

    def schemas(self, catalog: str) -> list[dict[str, Any]]:
        return self.request("GET", f"/catalogs/{catalog}/schemas")["schemas"]

    def tables(self, catalog: str, schema: str) -> list[dict[str, Any]]:
        return self.request("GET", f"/catalogs/{catalog}/schemas/{schema}/tables")["tables"]

    def table(self, full_name: str) -> dict[str, Any]:
        catalog, schema, name = full_name.split(".")
        return self.request("GET", f"/catalogs/{catalog}/schemas/{schema}/tables/{name}")

    def views(self, catalog: str, schema: str) -> list[dict[str, Any]]:
        return self.request("GET", f"/catalogs/{catalog}/schemas/{schema}/views")["views"]

    def search(self, text: str = "", *, limit: int = 25) -> list[dict[str, Any]]:
        """Tables and views you may read whose name, comment or a column matches `text`."""
        return self.request("GET", "/search", params={"q": text, "limit": limit})["results"]

    def describe(self, full_name: str, *, sample: int = 5) -> dict[str, Any]:
        """Columns, size, partitioning and a few sample rows of a table or view."""
        catalog, schema, name = full_name.split(".")
        base = f"/catalogs/{catalog}/schemas/{schema}"
        try:
            info = {"kind": "table", **self.request("GET", f"{base}/tables/{name}")}
        except DataError as exc:
            if exc.status != 404:
                raise
            info = {"kind": "view", **self.request("GET", f"{base}/views/{name}")}
        if sample:
            info["sample"] = self.sql(f"SELECT * FROM {full_name} LIMIT {int(sample)}")
        return info

    def create_view(self, full_name: str, sql: str, comment: str | None = None) -> dict[str, Any]:
        """Define a named query. Readers need SELECT on the view, not on the tables it reads."""
        catalog, schema, name = full_name.split(".")
        return self.request(
            "POST",
            f"/catalogs/{catalog}/schemas/{schema}/views",
            json={"name": name, "sql": sql, "comment": comment},
        )

    def create_schema(self, catalog: str, name: str, comment: str | None = None) -> dict[str, Any]:
        return self.request(
            "POST", f"/catalogs/{catalog}/schemas", json={"name": name, "comment": comment}
        )

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
            "POST",
            "/imports",
            json={
                "table": table,
                "mode": mode,
                "format": file_format,
                "partitioning": partitioning or [],
                "files": [{"name": p.name, "size": p.stat().st_size} for p in paths],
            },
        )
        completions = []
        with ThreadPoolExecutor(max_workers=parallelism) as pool:
            for path, upload in zip(paths, created["uploads"]):
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
                            for part, future in zip(upload["parts"], futures)
                        ],
                    }
                )
        record = self.request(
            "POST", f"/imports/{created['import']['id']}:complete", json={"files": completions}
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
            data = stream.read(min(length, max(0, path.stat().st_size - offset)))
        response = self.uploads.put(url, content=data)
        response.raise_for_status()
        return response.headers.get("ETag", "").strip('"')
