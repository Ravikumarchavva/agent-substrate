"""`client.sql`: run read-only SQL. Callable, with Arrow, streaming and plain-JSON forms.

df = client.sql("SELECT ...")                 # Polars DataFrame          (needs the [arrow] extra)
table = client.sql.arrow("SELECT ...")        # pyarrow.Table             (needs the [arrow] extra)
for batch in client.sql.stream("SELECT ..."): # record batches, bounded memory
rows = client.sql.rows("SELECT ...")          # JSON rows, no extra needed (bounded by max_rows)
"""

from __future__ import annotations

import asyncio
import io
import queue
import threading
from collections.abc import AsyncIterator, Iterator
from typing import Any

from loamz.core.exceptions import ResultTooLargeError
from loamz.core.transport import AsyncTransport, SyncTransport
from loamz.data import _arrow
from loamz.data.models import QueryResult

ARROW = "application/vnd.apache.arrow.stream"
# Default ceiling for one `sql()`/`arrow()` result held in memory. Sandboxes are small: ask SQL for
# the answer (aggregate, filter, LIMIT) or iterate `stream()` instead of pulling rows into Python.
DEFAULT_MAX_BYTES = 512 * 1024 * 1024


def _body(
    sql: str,
    catalog: str,
    schema: str,
    max_rows: int | None,
    compute_id: str | None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    body = {
        "sql": sql,
        "catalog": catalog,
        "schema": schema,
        "max_rows": max_rows,
        "compute_id": compute_id,
        "timeout_seconds": timeout_seconds,
    }
    return {k: v for k, v in body.items() if v is not None}


def _result(payload: dict[str, Any]) -> QueryResult:
    return QueryResult(
        columns=payload["columns"],
        rows=[list(r) for r in payload["rows"]],
        truncated=bool(payload.get("truncated")),
        statement_id=payload.get("statement_id"),
    )


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


def _raw(response: Any) -> Iterator[bytes]:
    """The body in chunks. (A response built in memory, as test transports do, is already read.)"""
    if response.is_stream_consumed:
        yield response.content
    else:
        yield from response.iter_raw()


async def _araw(response: Any) -> AsyncIterator[bytes]:
    if response.is_stream_consumed:
        yield response.content
    else:
        async for chunk in response.aiter_raw():
            yield chunk


def _queued(source: queue.Queue) -> Iterator[bytes]:
    while True:
        item = source.get()
        if item is None:
            return
        if isinstance(item, BaseException):
            raise item
        yield item


class Sql:
    def __init__(self, core: SyncTransport):
        self._core = core

    def __call__(self, query: str, **options: Any):
        """Run a read-only SELECT and get a Polars DataFrame. Filters and aggregation run server-side."""
        return _arrow.polars().from_arrow(self.arrow(query, **options))

    def stream(
        self,
        query: str,
        *,
        catalog: str = "main",
        schema: str = "default",
        max_rows: int | None = None,
        compute_id: str | None = None,
    ) -> Iterator[Any]:
        """Record batches as the worker produces them; memory stays bounded."""
        pa = _arrow.pyarrow()  # fail now, with the hint, not on the first iteration
        return self._batches(pa, query, catalog, schema, max_rows, compute_id)

    def _batches(self, pa, query, catalog, schema, max_rows, compute_id) -> Iterator[Any]:
        response = self._core.send(
            "POST",
            "/statements",
            json=_body(query, catalog, schema, max_rows, compute_id),
            headers={"Accept": ARROW},
            idempotent=True,  # a SELECT: nothing to repeat if the first attempt never ran
            stream=True,
        )
        try:
            self._core.check(response)
            # Arrow wants full reads; a raw adapter returns whatever chunk it holds. Buffer it.
            source = io.BufferedReader(_Reader(_raw(response)), buffer_size=1 << 20)
            yield from pa.ipc.open_stream(pa.PythonFile(source, mode="r"))
        finally:
            response.close()

    def arrow(
        self, query: str, *, max_bytes: int | None = DEFAULT_MAX_BYTES, **options: Any
    ) -> Any:
        """The whole result as one Arrow table, refusing to hold more than `max_bytes`."""
        pa = _arrow.pyarrow()
        batches: list[Any] = []
        size = 0
        for batch in self.stream(query, **options):
            size += batch.nbytes
            if max_bytes is not None and size > max_bytes:
                raise ResultTooLargeError(max_bytes)
            batches.append(batch)
        return pa.Table.from_batches(batches) if batches else pa.table({})

    def rows(
        self,
        query: str,
        *,
        catalog: str = "main",
        schema: str = "default",
        max_rows: int | None = None,
        compute_id: str | None = None,
    ) -> QueryResult:
        """A small result as JSON rows. Needs no Arrow; the server caps it at `max_rows`."""
        return _result(
            self._core.call(
                "POST",
                "/statements",
                json=_body(query, catalog, schema, max_rows, compute_id),
                idempotent=True,
            )
        )


class AsyncSql:
    def __init__(self, core: AsyncTransport):
        self._core = core

    async def __call__(self, query: str, **options: Any):
        return _arrow.polars().from_arrow(await self.arrow(query, **options))

    def stream(
        self,
        query: str,
        *,
        catalog: str = "main",
        schema: str = "default",
        max_rows: int | None = None,
        compute_id: str | None = None,
    ) -> AsyncIterator[Any]:
        """Record batches as they arrive. Arrow's reader blocks, so it runs in a worker thread fed
        through a small bounded queue; the event loop is never blocked."""
        pa = _arrow.pyarrow()  # fail now, with the hint, not on the first iteration
        return self._batches(pa, query, catalog, schema, max_rows, compute_id)

    async def _batches(
        self, pa, query, catalog, schema, max_rows, compute_id
    ) -> AsyncIterator[Any]:
        response = await self._core.send(
            "POST",
            "/statements",
            json=_body(query, catalog, schema, max_rows, compute_id),
            headers={"Accept": ARROW},
            idempotent=True,
            stream=True,
        )
        chunks: queue.Queue = queue.Queue(maxsize=16)
        stop = threading.Event()

        def put(item: Any) -> None:
            while not stop.is_set():
                try:
                    chunks.put(item, timeout=0.2)
                    return
                except queue.Full:
                    continue

        async def feed() -> None:
            try:
                async for chunk in _araw(response):
                    await asyncio.to_thread(put, chunk)
                await asyncio.to_thread(put, None)
            except Exception as exc:  # noqa: BLE001 - handed to the reader thread
                await asyncio.to_thread(put, exc)

        feeder = None
        try:
            await self._core.check(response)
            feeder = asyncio.create_task(feed())
            source = io.BufferedReader(_Reader(_queued(chunks)), buffer_size=1 << 20)
            # Opening the stream reads its schema, which blocks on the queue: do it off the loop too.
            batches = await asyncio.to_thread(
                lambda: iter(pa.ipc.open_stream(pa.PythonFile(source, mode="r")))
            )
            while (batch := await asyncio.to_thread(next, batches, None)) is not None:
                yield batch
        finally:
            stop.set()
            if feeder is not None:
                feeder.cancel()
            await response.aclose()

    async def arrow(
        self, query: str, *, max_bytes: int | None = DEFAULT_MAX_BYTES, **options: Any
    ) -> Any:
        pa = _arrow.pyarrow()
        batches: list[Any] = []
        size = 0
        async for batch in self.stream(query, **options):
            size += batch.nbytes
            if max_bytes is not None and size > max_bytes:
                raise ResultTooLargeError(max_bytes)
            batches.append(batch)
        return pa.Table.from_batches(batches) if batches else pa.table({})

    async def rows(
        self,
        query: str,
        *,
        catalog: str = "main",
        schema: str = "default",
        max_rows: int | None = None,
        compute_id: str | None = None,
    ) -> QueryResult:
        return _result(
            await self._core.call(
                "POST",
                "/statements",
                json=_body(query, catalog, schema, max_rows, compute_id),
                idempotent=True,
            )
        )
