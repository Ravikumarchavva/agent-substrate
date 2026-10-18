"""Load NYC yellow taxi months into main.nyc.yellow, partitioned by pickup month.

Uses the files in .workspace/datasets/nyc_taxi/yellow (download them from the TLC site). Files go
straight to object storage; the attached jobs worker normalizes and partitions them once.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from dotenv import dotenv_values
from substrate_data_client import ClientCredentials, DataClient, DataError

ENV = {**dotenv_values(".env"), **os.environ}

ROOT = Path(__file__).resolve().parent.parent
files = sorted((ROOT / ".workspace/datasets/nyc_taxi/yellow").glob("yellow_tripdata_*.parquet"))
if not files:
    sys.exit(
        "No files in .workspace/datasets/nyc_taxi/yellow; download TLC yellow_tripdata_*.parquet first"
    )
client = DataClient(
    ENV.get("SUBSTRATE_DATA_API_URL", "http://127.0.0.1:8020"),
    token=ClientCredentials(
        ENV["SUBSTRATE_DATA_OIDC_ISSUER"], "substrate-data-bench", ENV["BENCH_CLIENT_SECRET"]
    ),
)
try:
    client.create_schema("main", "nyc", "NYC Taxi & Limousine Commission trip records")
except DataError as exc:
    if exc.status != 409:
        raise
started = time.perf_counter()
record = client.upload(
    "main.nyc.yellow",
    files,
    partitioning=[{"column": "tpep_pickup_datetime", "transform": "month"}],
)
print(
    f"Imported {record['rows']:,} rows from {len(files)} files in {time.perf_counter() - started:.1f}s"
)


VIEWS = {
    "monthly_revenue": (
        "Trips and revenue per month over the latest 12 published months",
        "SELECT date_trunc('month', tpep_pickup_datetime) AS month, count(*) AS trips, "
        "round(sum(total_amount), 2) AS revenue, round(avg(trip_distance), 2) AS avg_distance "
        "FROM main.nyc.yellow "
        "WHERE tpep_pickup_datetime >= '2025-09-01' AND tpep_pickup_datetime < '2026-09-01' "
        "GROUP BY 1",
    ),
    "busiest_zones_august": (
        "Pickup zones ranked by trips in August 2026",
        "SELECT PULocationID AS zone, count(*) AS trips, round(avg(fare_amount), 2) AS avg_fare "
        "FROM main.nyc.yellow "
        "WHERE tpep_pickup_datetime >= '2026-08-01' AND tpep_pickup_datetime < '2026-09-01' "
        "GROUP BY 1 ORDER BY trips DESC",
    ),
    "long_trips": (
        "Trips over 20 miles since August 2026",
        "SELECT tpep_pickup_datetime, trip_distance, fare_amount, tip_amount, PULocationID, DOLocationID "
        "FROM main.nyc.yellow WHERE tpep_pickup_datetime >= '2026-08-01' AND trip_distance > 20",
    ),
}
for name, (comment, sql) in VIEWS.items():
    try:
        view = client.create_view(f"main.nyc.{name}", sql, comment)
        print(f"Created view {view['full_name']} ({', '.join(c['name'] for c in view['columns'])})")
    except DataError as exc:
        if exc.status != 409:
            raise
        print(f"View main.nyc.{name} already exists")
