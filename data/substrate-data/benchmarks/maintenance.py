"""Milestone 3 effects on the real taxi table: result cache, materialized views, sort clustering.

Needs the stack (`make up && make dev && make dev-compute && make demo`). Creates two objects under
main.nyc (a materialized aggregate and a 3-month slice it then sorts) and drops them at the end.

    uv run python benchmarks/maintenance.py
"""

from __future__ import annotations

import os
import statistics
import time

from dotenv import dotenv_values
from substrate_data_client import ClientCredentials, DataClient

ENV = {**dotenv_values(".env"), **os.environ}
client = DataClient(
    "http://127.0.0.1:8020",
    token=ClientCredentials(
        ENV["SUBSTRATE_DATA_OIDC_ISSUER"], "substrate-data-bench", ENV["BENCH_CLIENT_SECRET"]
    ),
)


def statement(sql: str) -> dict:
    return client.request("POST", "/statements", json={"sql": sql, "max_rows": 100000})


SALT = iter(range(int(time.time()) % 100000 * 10, 10**9))


def fresh(sql: str) -> dict:
    """Run `sql` as a statement the worker has never seen: same plan and cost, different text.
    (The result cache keys on the normalized SQL, so a comment would not be enough.)"""
    return statement(f"SELECT * FROM ({sql}) AS q LIMIT {next(SALT)}")


def median_ms(sql: str, runs: int = 5, *, cached: bool = False) -> float:
    samples = []
    for _ in range(runs):
        start = time.perf_counter()
        statement(sql) if cached else fresh(sql)
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples)


def wait(path: str, done) -> dict:
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        value = client.request("GET", path)
        if done(value):
            return value
        time.sleep(1)
    raise TimeoutError(path)


def run_status(table: str, run_id: str) -> dict:
    path = f"/catalogs/main/schemas/nyc/tables/{table}/maintenance-runs/{run_id}"
    return wait(path, lambda r: r["status"] in {"succeeded", "failed"})


print("== 1. worker result cache (same SQL over the same files)")
for label, sql in (
    (
        "June-August zones",
        "SELECT PULocationID, count(*) n, avg(fare_amount) a FROM main.nyc.yellow "
        "WHERE tpep_pickup_datetime >= '2026-06-01' GROUP BY 1 ORDER BY n DESC",
    ),
    (
        "all 88M rows",
        "SELECT count(*) n, avg(total_amount) a, max(trip_distance) d FROM main.nyc.yellow",
    ),
):
    first = fresh(sql)["duration_ms"]
    executed = median_ms(sql)
    statement(sql)  # remembered now
    hit = median_ms(sql, 15, cached=True)
    print(
        f"  {label:18s} first run {first:7.1f} ms   re-executed (warm) {executed:7.1f} ms   served from cache {hit:6.1f} ms"
    )

print("== 2. materialized view: an aggregate over 88M rows, stored once")
aggregate = (
    "SELECT date_trunc('month', tpep_pickup_datetime) AS month, PULocationID AS zone, "
    "count(*) AS trips, sum(total_amount) AS revenue FROM main.nyc.yellow GROUP BY 1, 2"
)
recompute = f"SELECT month, sum(revenue) AS revenue FROM ({aggregate}) GROUP BY 1 ORDER BY 1"
base_ms = median_ms(recompute)
start = time.perf_counter()
created = client.request(
    "POST",
    "/catalogs/main/schemas/nyc/materialized-views",
    json={"name": "bench_zone_month", "sql": aggregate},
)
view = wait(
    "/catalogs/main/schemas/nyc/tables/bench_zone_month",
    lambda t: t["materialized"]["status"] in {"fresh", "failed"},
)
build = time.perf_counter() - start
print(
    f"  built in {build:5.1f} s -> {view['row_count']:,} rows, {view['size_bytes'] / 1e6:.1f} MB, status {view['materialized']['status']}"
)
reread = (
    "SELECT month, sum(revenue) AS revenue FROM main.nyc.bench_zone_month GROUP BY 1 ORDER BY 1"
)
mv_ms = median_ms(reread)
print(
    f"  same answer from the base table: {base_ms:8.1f} ms   "
    f"from the materialized view: {mv_ms:6.1f} ms   (medians of 5 real executions)"
)

print("== 3. sort clustering on a slice of the table")
SLICE_FROM = ENV.get("SLICE_FROM", "2026-06-01")
slice_sql = f"SELECT * FROM main.nyc.yellow WHERE tpep_pickup_datetime >= '{SLICE_FROM}'"
client.request(
    "POST",
    "/catalogs/main/schemas/nyc/materialized-views",
    json={"name": "bench_slice", "sql": slice_sql},
)
slice_view = wait(
    "/catalogs/main/schemas/nyc/tables/bench_slice",
    lambda t: t["materialized"]["status"] in {"fresh", "failed"},
)
print(f"  slice: {slice_view['row_count']:,} rows in {slice_view['file_count']} files")
probe = "SELECT count(*) n, avg(fare_amount) a FROM main.nyc.bench_slice WHERE PULocationID = 132"
before = fresh(probe)
print(f"  before: scans {before['files_scanned']} files, {median_ms(probe):6.1f} ms median")
start = time.perf_counter()
run = client.request(
    "POST",
    "/catalogs/main/schemas/nyc/tables/bench_slice:optimize",
    json={"strategy": "sort", "sort_by": ["PULocationID"], "target_file_mb": 16},
)
done = run_status("bench_slice", run["id"])
print(
    f"  sorted in {time.perf_counter() - start:5.1f} s: {done['files_in']} -> {done['files_out']} files, status {done['status']}"
)
after = fresh(probe)
print(
    f"  after:  scans {after['files_scanned']} of {done['files_out']} files, {median_ms(probe):6.1f} ms median"
)

for name in ("bench_zone_month", "bench_slice"):
    client.request("DELETE", f"/catalogs/main/schemas/nyc/tables/{name}")
print("cleaned up")
