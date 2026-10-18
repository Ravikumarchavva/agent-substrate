"""Query latency by class against main.nyc.yellow (run `make demo` first).

Reports end-to-end p50/p99 as a client sees it (Arrow path), plus server-side plan/compute split
and files scanned from the JSON path. Measured, not guaranteed: results depend on hardware.
"""

from __future__ import annotations

import os
import statistics
import time

from dotenv import dotenv_values
from substrate_data_client import ClientCredentials, DataClient

ENV = {**dotenv_values(".env"), **os.environ}

API = ENV.get("SUBSTRATE_DATA_API_URL", "http://127.0.0.1:8020")
QUERIES = {
    "trivial": "SELECT 42 AS answer",
    "metadata count(*)": "SELECT count(*) FROM nyc.yellow",
    "point lookup (1 hour, 1 zone)": (
        "SELECT * FROM nyc.yellow WHERE tpep_pickup_datetime >= '2026-08-15 08:00:00' "
        "AND tpep_pickup_datetime < '2026-08-15 09:00:00' AND PULocationID = 161"
    ),
    "one month aggregate": (
        "SELECT PULocationID, count(*) trips, sum(total_amount) revenue FROM nyc.yellow "
        "WHERE tpep_pickup_datetime >= '2026-08-01' AND tpep_pickup_datetime < '2026-09-01' "
        "GROUP BY 1 ORDER BY revenue DESC LIMIT 10"
    ),
    "full-table aggregate (88M rows)": (
        "SELECT date_trunc('month', tpep_pickup_datetime) AS month, count(*) trips, "
        "avg(trip_distance) distance FROM nyc.yellow GROUP BY 1 ORDER BY 1"
    ),
}


def main() -> None:
    client = DataClient(
        API,
        token=ClientCredentials(
            ENV["SUBSTRATE_DATA_OIDC_ISSUER"], "substrate-data-bench", ENV["BENCH_CLIENT_SECRET"]
        ),
    )
    print(
        f"{'query':34s} {'p50 ms':>8s} {'p99 ms':>8s} {'plan':>6s} {'compute':>8s} {'files':>9s} {'rows':>6s}"
    )
    for name, sql in QUERIES.items():
        runs = 5 if "full" in name else 25
        client.sql(sql)  # warm caches
        times = []
        for _ in range(runs):
            started = time.perf_counter()
            frame = client.sql(sql)
            times.append((time.perf_counter() - started) * 1000)
        meta = client.request("POST", "/statements", json={"sql": sql, "max_rows": 1})
        times.sort()
        p99 = times[min(len(times) - 1, int(len(times) * 0.99))]
        print(
            f"{name:34s} {statistics.median(times):8.1f} {p99:8.1f} {meta['timing_ms']['plan']:6.1f} "
            f"{meta['timing_ms']['compute']:8.1f} {meta['files_scanned']:9d} {frame.height:6d}"
        )


if __name__ == "__main__":
    main()
