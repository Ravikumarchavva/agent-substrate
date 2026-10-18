"""Separate OS processes, configured like `make dev` + `make dev-compute` (opt-in)."""

from __future__ import annotations

import polars as pl
import pytest

from loamz.data.client import DataError

pytestmark = pytest.mark.integration


def test_import_and_query_across_processes(process_stack, tmp_path):
    client = process_stack.client()
    client.create_schema("main", "proc")
    path = tmp_path / "numbers.csv"
    pl.DataFrame({"v": range(10_000), "k": [i % 7 for i in range(10_000)]}).write_csv(path)
    assert client.upload("main.proc.numbers", [path])["rows"] == 10_000
    result = client.sql("SELECT k, sum(v) AS total FROM proc.numbers GROUP BY k ORDER BY k")
    assert result["total"].sum() == sum(range(10_000))
    # Tokens are verified against the IdP's published keys over HTTP (real JWKS client).
    bob = process_stack.client("bob", "analysts")
    with pytest.raises(DataError) as caught:
        bob.sql("SELECT * FROM main.proc.numbers")
    assert caught.value.status == 403  # compute attached outside the API has no grants yet
    for resource in client.request("GET", "/compute/resources")["resources"]:
        client.request(
            "PATCH",
            f"/grants/compute/{resource['id']}",
            json={"changes": [{"principal": "group:account users", "add": ["USE_COMPUTE"]}]},
        )
    with pytest.raises(DataError) as caught:
        bob.sql("SELECT * FROM main.proc.numbers")
    assert caught.value.status == 404  # table existence is not disclosed


def test_a_dead_sql_worker_fails_fast_and_recovers(process_stack):
    client = process_stack.client()
    assert client.sql("SELECT 1 AS one").item() == 1
    process_stack.kill("sql")
    with pytest.raises(DataError) as caught:
        client.sql("SELECT 1 AS one", compute_id=None)
    assert caught.value.status in {502, 503}
    process_stack.start("sql")
    assert client.sql("SELECT 2 AS two").item() == 2
