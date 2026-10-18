"""DuckDB vs Polars on the same Parquet files in the same object store (main.nyc.yellow).

Both engines read the table's data files from S3 with all cores, warm, and must return the same
answer; the script checks that before timing. Run `make demo` first, then `make bench-engines`.
"""

from __future__ import annotations

import os
import statistics
import sys
import time
from collections.abc import Callable
from pathlib import Path

import duckdb
import polars as pl
from dotenv import dotenv_values
from polars.testing import assert_frame_equal
from pyiceberg.catalog import load_catalog

ENV = {**dotenv_values(".env"), **os.environ}
REPEAT = 7


def files() -> list[str]:
    key, secret = ENV["CONTROL_S3_ACCESS_KEY_ID"], ENV["CONTROL_S3_SECRET_ACCESS_KEY"]
    url = ENV["SUBSTRATE_DATA_ICEBERG_CATALOG_URL"]
    catalog = load_catalog(
        "bench",
        **{
            "type": "rest",
            "uri": url,
            "oauth2-server-uri": url + "/v1/oauth/tokens",
            "warehouse": "s3://warehouse/",
            "credential": f"{key}:{secret}",
            "s3.endpoint": ENV["SUBSTRATE_DATA_S3_ENDPOINT_URL"],
            "s3.access-key-id": key,
            "s3.secret-access-key": secret,
            "s3.region": "us-east-1",
        },
    )
    table = catalog.load_table(("main", "nyc", "yellow"))
    return [task.file.file_path for task in table.scan().plan_files()]


def localize(paths: list[str]) -> list[str]:
    """Copy the data files to local disk once, to separate engine speed from object-store clients."""
    import pyarrow.fs as pafs

    fs = pafs.S3FileSystem(
        access_key=ENV["WORKER_S3_ACCESS_KEY_ID"],
        secret_key=ENV["WORKER_S3_SECRET_ACCESS_KEY"],
        endpoint_override=ENV["SUBSTRATE_DATA_S3_ENDPOINT_URL"].removeprefix("http://"),
        scheme="http",
        region="us-east-1",
    )
    root = Path(ENV.get("BENCH_LOCAL_DIR", ".workspace/bench-local"))
    local = []
    for index, path in enumerate(paths):
        target = root / f"{index:03d}.parquet"
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            with fs.open_input_stream(path.removeprefix("s3://")) as src, target.open("wb") as out:
                while chunk := src.read(8 << 20):
                    out.write(chunk)
        local.append(str(target.resolve()))
    return local


def engines(paths: list[str]):
    endpoint = ENV["SUBSTRATE_DATA_S3_ENDPOINT_URL"]
    key, secret = ENV["WORKER_S3_ACCESS_KEY_ID"], ENV["WORKER_S3_SECRET_ACCESS_KEY"]
    db = duckdb.connect()
    db.execute("LOAD httpfs")
    db.execute(
        "CREATE SECRET s (TYPE S3, KEY_ID ?, SECRET ?, ENDPOINT ?, URL_STYLE 'path', USE_SSL false,"
        " REGION 'us-east-1')",
        [key, secret, endpoint.removeprefix("http://")],
    )
    db.execute("SET parquet_metadata_cache=true")
    listing = ", ".join("'" + p + "'" for p in paths)
    db.execute(
        f"CREATE VIEW yellow AS SELECT * FROM read_parquet([{listing}], union_by_name=true, hive_partitioning=false)"
    )
    options = {
        "aws_access_key_id": key,
        "aws_secret_access_key": secret,
        "aws_endpoint_url": endpoint,
        "aws_region": "us-east-1",
        "aws_allow_http": "true",
    }
    lazy = pl.scan_parquet(
        paths, storage_options=options, allow_missing_columns=True, hive_partitioning=False
    )
    return db, lazy


def median_ms(run: Callable[[], object]) -> tuple[float, object]:
    result = run()  # warm
    times = []
    for _ in range(REPEAT):
        started = time.perf_counter()
        result = run()
        times.append((time.perf_counter() - started) * 1000)
    return statistics.median(times), result


def agree(left: pl.DataFrame, right: pl.DataFrame) -> bool:
    """Same rows and values (floats to 1e-6), whatever order the engines produced them in."""
    try:
        columns = sorted(left.columns)
        assert_frame_equal(
            left.select(columns).sort(columns),
            right.select(columns).sort(columns),
            check_dtype=False,
            check_exact=False,
            rel_tol=1e-6,
        )
    except AssertionError:
        return False
    return True


def main() -> None:
    paths = files()
    local = "--local" in sys.argv
    if local:
        paths = localize(paths)
    db, lazy = engines(paths)
    window = (pl.col("tpep_pickup_datetime") >= pl.datetime(2026, 8, 1)) & (
        pl.col("tpep_pickup_datetime") < pl.datetime(2026, 9, 1)
    )
    hour = (pl.col("tpep_pickup_datetime") >= pl.datetime(2026, 8, 15, 8)) & (
        pl.col("tpep_pickup_datetime") < pl.datetime(2026, 8, 15, 9)
    )
    cases = {
        "count(*)": (
            "SELECT count(*) AS n FROM yellow",
            lazy.select(pl.len().alias("n")),
        ),
        "one-hour point lookup": (
            "SELECT * FROM yellow WHERE tpep_pickup_datetime >= '2026-08-15 08:00' "
            "AND tpep_pickup_datetime < '2026-08-15 09:00' AND PULocationID = 161",
            lazy.filter(hour & (pl.col("PULocationID") == 161)),
        ),
        "one-month aggregate": (
            "SELECT PULocationID, count(*) AS trips, sum(total_amount) AS revenue FROM yellow "
            "WHERE tpep_pickup_datetime >= '2026-08-01' AND tpep_pickup_datetime < '2026-09-01' "
            "GROUP BY 1",
            lazy.filter(window)
            .group_by("PULocationID")
            .agg(pl.len().alias("trips"), pl.col("total_amount").sum().alias("revenue")),
        ),
        "full scan, 1 column sum": (
            "SELECT sum(total_amount) AS s FROM yellow",
            lazy.select(pl.col("total_amount").sum().alias("s")),
        ),
        "full group by month": (
            "SELECT date_trunc('month', tpep_pickup_datetime) AS m, count(*) AS n, "
            "avg(trip_distance) AS d FROM yellow GROUP BY 1",
            # Polars' fastest correct form: group on integer year and month, build the date after.
            # (`dt.truncate("1mo")` gives the same answer ~3x slower; `dt.month_start()` keeps the
            # time of day and is wrong for this purpose.)
            lazy.group_by(
                pl.col("tpep_pickup_datetime").dt.year().alias("y"),
                pl.col("tpep_pickup_datetime").dt.month().alias("mo"),
            )
            .agg(pl.len().alias("n"), pl.col("trip_distance").mean().alias("d"))
            .select(pl.datetime(pl.col("y"), pl.col("mo"), 1).alias("m"), "n", "d"),
        ),
        "full group by zone, top 10": (
            "SELECT PULocationID, count(*) AS n, sum(total_amount) AS r FROM yellow "
            "GROUP BY 1 ORDER BY r DESC LIMIT 10",
            lazy.group_by("PULocationID")
            .agg(pl.len().alias("n"), pl.col("total_amount").sum().alias("r"))
            .sort("r", descending=True)
            .head(10),
        ),
        "full distinct count (high cardinality)": (
            "SELECT count(DISTINCT tpep_pickup_datetime) AS n FROM yellow",
            lazy.select(pl.col("tpep_pickup_datetime").n_unique().alias("n")),
        ),
    }
    print(
        f"{len(paths)} files from {'local disk' if local else 'S3'}, {pl.thread_pool_size()} cores. "
        f"Median of {REPEAT} warm runs, ms.\n"
    )
    print(f"{'query':40s} {'DuckDB':>9s} {'Polars':>9s} {'Polars stream':>14s}  agree")
    totals = [0.0, 0.0, 0.0]
    for name, (sql, frame) in cases.items():
        duck_ms, duck = median_ms(lambda sql=sql: db.execute(sql).pl())
        pol_ms, pol = median_ms(lambda frame=frame: frame.collect())
        stream_ms, _ = median_ms(lambda frame=frame: frame.collect(engine="streaming"))
        same = agree(duck, pol)
        for index, value in enumerate((duck_ms, pol_ms, stream_ms)):
            totals[index] += value
        print(
            f"{name:40s} {duck_ms:9.0f} {pol_ms:9.0f} {stream_ms:14.0f}  {'yes' if same else 'NO'}"
        )
    print(f"{'total':40s} {totals[0]:9.0f} {totals[1]:9.0f} {totals[2]:14.0f}")


if __name__ == "__main__":
    main()
