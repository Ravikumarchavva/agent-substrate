"""Arrow and Polars results (the dev environment installs the [arrow] extra)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

import substrate_data_client as sd
from substrate_data_client import AsyncDataClient, DataClient, NotFound

pa = pytest.importorskip("pyarrow")
pytest.importorskip("polars")


def arrow_response(rows: int = 5) -> httpx.Response:
    table = pa.table({"n": list(range(rows)), "s": [f"row{i}" for i in range(rows)]})
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        for batch in table.to_batches(max_chunksize=2):
            writer.write_batch(batch)
    return httpx.Response(
        200,
        content=sink.getvalue().to_pybytes(),
        headers={"content-type": "application/vnd.apache.arrow.stream"},
    )


def sync_client(handler) -> DataClient:
    return DataClient("http://data.test", token="t", transport=httpx.MockTransport(handler))


def test_arrow_and_polars_results_stream_from_the_statements_route():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return arrow_response(5)

    api = sync_client(handler)
    frame = api.sql("SELECT n, s FROM t", max_rows=10)
    assert frame.shape == (5, 2) and frame["s"][4] == "row4"
    assert seen[0].headers["accept"] == "application/vnd.apache.arrow.stream"
    assert json.loads(seen[0].content) == {
        "sql": "SELECT n, s FROM t",
        "catalog": "main",
        "schema": "default",
        "max_rows": 10,
    }
    assert [b.num_rows for b in api.sql.stream("SELECT 1")] == [2, 2, 1]
    assert api.arrow("SELECT 1").num_rows == 5
    with pytest.raises(sd.ResultTooLarge):
        api.sql.arrow("SELECT 1", max_bytes=10)


def test_the_async_client_streams_arrow_batches():
    async def run():
        async with AsyncDataClient(
            "http://data.test", transport=httpx.MockTransport(lambda r: arrow_response(7))
        ) as api:
            seen = [batch.num_rows async for batch in api.sql.stream("SELECT 1")]
            table = await api.sql.arrow("SELECT 1")
            frame = await api.sql("SELECT 1")
            return seen, table.num_rows, frame.height

    seen, rows, height = asyncio.run(run())
    assert seen == [2, 2, 2, 1] and rows == 7 and height == 7


def test_a_failed_statement_raises_the_problem_before_any_batch():
    def handler(request):
        return httpx.Response(
            404, json={"type": "https://substrate.dev/problems/not-found", "detail": "no table"}
        )

    api = sync_client(handler)
    with pytest.raises(NotFound):
        api.sql.arrow("SELECT * FROM main.x.y")
    with pytest.raises(NotFound):
        list(api.sql.stream("SELECT * FROM main.x.y"))

    async def run():
        async with AsyncDataClient("http://data.test", transport=httpx.MockTransport(handler)) as a:
            return [b async for b in a.sql.stream("SELECT 1")]

    with pytest.raises(NotFound):
        asyncio.run(run())
