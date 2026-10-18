"""Milestone 0 spike: Delta (delta-rs) vs Iceberg (SeaweedFS 4.48 REST catalog) on NYC yellow taxi 2024.

Needs a SeaweedFS 4.48 `weed mini` on 127.0.0.1:28333 (S3) / :28181 (Iceberg REST) with a `delta`
bucket and a `warehouse` table bucket, TAXI_DIR pointing at yellow_tripdata_2024-*.parquet, and
SPIKE_S3_ACCESS_KEY / SPIKE_S3_SECRET_KEY. Results: docs/decisions/0001-table-format.md.

Usage: table_format.py <step> ; each step prints one JSON line of measurements.
"""

from __future__ import annotations

import json
import os
import resource
import sys
import threading
import time
from pathlib import Path

import duckdb
import polars as pl

HERE = Path(__file__).parent
SOURCE = Path(os.environ["TAXI_DIR"])
AK, SK = os.environ["SPIKE_S3_ACCESS_KEY"], os.environ["SPIKE_S3_SECRET_KEY"]
S3 = "http://127.0.0.1:28333"
ICEBERG = "http://127.0.0.1:28181"
DELTA_URI = "s3://delta/yellow"
DELTA_OPTS = {
    "AWS_ACCESS_KEY_ID": AK,
    "AWS_SECRET_ACCESS_KEY": SK,
    "AWS_ENDPOINT_URL": S3,
    "AWS_REGION": "us-east-1",
    "AWS_ALLOW_HTTP": "true",
    "AWS_S3_ALLOW_UNSAFE_RENAME": "false",
    "conditional_put": "etag",
}
FILTER_MONTH = "2024-06"


def emit(step: str, **values) -> None:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
    print(json.dumps({"step": step, "peak_rss_mb": peak, **values}), flush=True)


def month_frame(path: Path) -> pl.DataFrame:
    frame = pl.read_parquet(path)
    month = path.stem.rsplit("_", 1)[1]
    # Source files contain a few out-of-range timestamps; partition by the file's month.
    # Months drift between ns and us timestamps; Iceberg v2 supports us only.
    return frame.with_columns(
        pl.col("tpep_pickup_datetime", "tpep_dropoff_datetime").dt.cast_time_unit("us"),
        pickup_month=pl.lit(month),
    )


def months() -> list[Path]:
    return sorted(SOURCE.glob("yellow_tripdata_2024-*.parquet"))


# ---------------------------------------------------------------- Delta
def delta_write() -> None:
    from deltalake import write_deltalake

    started = time.perf_counter()
    rows = 0
    for path in months():
        frame = month_frame(path)
        rows += frame.height
        write_deltalake(
            DELTA_URI,
            frame.to_arrow(),
            mode="append",
            partition_by=["pickup_month"],
            storage_options=DELTA_OPTS,
        )
    emit(
        "delta_write",
        rows=rows,
        commits=len(months()),
        seconds=round(time.perf_counter() - started, 2),
    )


def query_polars(frame: pl.LazyFrame) -> pl.DataFrame:
    return (
        frame.filter((pl.col("pickup_month") == FILTER_MONTH) & (pl.col("passenger_count") > 3))
        .group_by("PULocationID")
        .agg(pl.len().alias("trips"), pl.col("total_amount").sum().alias("revenue"))
        .sort("revenue", descending=True)
        .head(5)
        .collect()
    )


def same(a: pl.DataFrame, b: pl.DataFrame) -> bool:
    def norm(f):
        return [
            (int(r[0]), int(r[1]), round(float(r[2]), 2))
            for r in f.sort("revenue", descending=True).rows()
        ]

    return norm(a) == norm(b)


ICEBERG_STORAGE = {
    "aws_access_key_id": AK,
    "aws_secret_access_key": SK,
    "aws_endpoint_url": S3,
    "aws_region": "us-east-1",
    "aws_allow_http": "true",
}


def timed(fn, repeat=3):
    times = []
    result = None
    for _ in range(repeat):
        started = time.perf_counter()
        result = fn()
        times.append(round((time.perf_counter() - started) * 1000, 1))
    return result, times


def delta_read() -> None:
    from deltalake import DeltaTable

    open_ms = timed(lambda: DeltaTable(DELTA_URI, storage_options=DELTA_OPTS), 3)[1]
    table = DeltaTable(DELTA_URI, storage_options=DELTA_OPTS)
    files_all = len(table.file_uris())
    files_pruned = len(table.file_uris(partition_filters=[("pickup_month", "=", FILTER_MONTH)]))  # noqa
    result, polars_ms = timed(
        lambda: query_polars(pl.scan_delta(DELTA_URI, storage_options=DELTA_OPTS))
    )
    full, full_ms = timed(
        lambda: pl.scan_delta(DELTA_URI, storage_options=DELTA_OPTS).select(pl.len()).collect(), 2
    )
    con = duckdb.connect()
    con.execute("INSTALL delta; LOAD delta;")
    con.execute(
        f"CREATE SECRET s (TYPE S3, KEY_ID '{AK}', SECRET '{SK}', ENDPOINT '127.0.0.1:28333',"
        " URL_STYLE 'path', USE_SSL false, REGION 'us-east-1')"
    )
    sql = f"""SELECT PULocationID, count(*) trips, sum(total_amount) revenue
              FROM delta_scan('{DELTA_URI}') WHERE pickup_month = '{FILTER_MONTH}' AND passenger_count > 3
              GROUP BY 1 ORDER BY revenue DESC LIMIT 5"""
    duck, duck_ms = timed(lambda: con.execute(sql).pl())
    emit(
        "delta_read",
        open_ms=open_ms,
        files_all=files_all,
        files_after_partition_prune=files_pruned,
        polars_ms=polars_ms,
        duckdb_ms=duck_ms,
        full_count=full.item(),
        full_count_ms=full_ms,
        same_answer=same(duck, result),
    )


def delta_concurrent() -> None:
    from deltalake import DeltaTable, write_deltalake

    table = DeltaTable(DELTA_URI, storage_options=DELTA_OPTS)
    before = table.version()
    sample = month_frame(months()[0]).head(1000).to_arrow()
    errors = []

    def append():
        try:
            write_deltalake(
                DELTA_URI,
                sample,
                mode="append",
                partition_by=["pickup_month"],
                storage_options=DELTA_OPTS,
            )
        except Exception as exc:  # report, the spike measures behaviour
            errors.append(repr(exc)[:300])

    threads = [threading.Thread(target=append) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    table = DeltaTable(DELTA_URI, storage_options=DELTA_OPTS)
    emit(
        "delta_concurrent",
        writers=4,
        version_before=before,
        version_after=table.version(),
        errors=errors,
    )


def delta_schema() -> None:
    from deltalake import DeltaTable, write_deltalake

    extra = month_frame(months()[0]).head(10).with_columns(pl.lit("spike").alias("note")).to_arrow()
    write_deltalake(
        DELTA_URI,
        extra,
        mode="append",
        schema_mode="merge",
        partition_by=["pickup_month"],
        storage_options=DELTA_OPTS,
    )
    names = DeltaTable(DELTA_URI, storage_options=DELTA_OPTS).schema().to_arrow().names
    emit("delta_schema", added_column="note" in names)


def delta_optimize() -> None:
    from deltalake import DeltaTable

    table = DeltaTable(DELTA_URI, storage_options=DELTA_OPTS)
    started = time.perf_counter()
    metrics = table.optimize.z_order(
        ["PULocationID"], partition_filters=[("pickup_month", "=", FILTER_MONTH)]
    )
    emit(
        "delta_zorder_one_month",
        seconds=round(time.perf_counter() - started, 1),
        files_added=metrics.get("numFilesAdded"),
        files_removed=metrics.get("numFilesRemoved"),
    )


# ---------------------------------------------------------------- Iceberg
def catalog():
    from pyiceberg.catalog import load_catalog

    return load_catalog(
        "seaweed",
        **{
            "type": "rest",
            "uri": ICEBERG,
            "warehouse": "s3://warehouse/",
            "credential": f"{AK}:{SK}",
            "s3.endpoint": S3,
            "s3.access-key-id": AK,
            "s3.secret-access-key": SK,
            "s3.region": "us-east-1",
        },
    )


def iceberg_write() -> None:
    from pyiceberg.transforms import IdentityTransform

    cat = catalog()
    try:
        cat.create_namespace("taxi")
    except Exception:  # already exists on reruns
        pass
    sample = month_frame(months()[0]).head(1).to_arrow()
    table = cat.create_table("taxi.yellow", schema=sample.schema)
    field = table.schema().find_field("pickup_month")
    with table.update_spec() as spec:
        spec.add_field(field.name, IdentityTransform(), "pickup_month")
    started = time.perf_counter()
    rows = 0
    for path in months():
        frame = month_frame(path).to_arrow()
        rows += frame.num_rows
        table.append(frame)
    emit(
        "iceberg_write",
        rows=rows,
        commits=len(months()),
        seconds=round(time.perf_counter() - started, 2),
    )


def iceberg_read() -> None:
    cat = catalog()
    open_ms = timed(lambda: cat.load_table("taxi.yellow"), 3)[1]
    table = cat.load_table("taxi.yellow")
    from pyiceberg.expressions import EqualTo

    files_all = len(list(table.scan().plan_files()))
    files_pruned = len(
        list(table.scan(row_filter=EqualTo("pickup_month", FILTER_MONTH)).plan_files())
    )
    result, polars_ms = timed(
        lambda: query_polars(pl.scan_iceberg(table, storage_options=ICEBERG_STORAGE))
    )
    full, full_ms = timed(
        lambda: pl.scan_iceberg(table, storage_options=ICEBERG_STORAGE).select(pl.len()).collect(),
        2,
    )
    con = duckdb.connect()
    con.execute("INSTALL iceberg; LOAD iceberg; INSTALL httpfs; LOAD httpfs;")
    con.execute(
        f"CREATE SECRET ice (TYPE ICEBERG, ENDPOINT '{ICEBERG}', CLIENT_ID '{AK}', CLIENT_SECRET '{SK}')"
    )
    con.execute(
        f"CREATE SECRET s3s (TYPE S3, KEY_ID '{AK}', SECRET '{SK}', ENDPOINT '127.0.0.1:28333',"
        " URL_STYLE 'path', USE_SSL false, REGION 'us-east-1')"
    )
    con.execute(f"ATTACH 's3://warehouse' AS wh (TYPE ICEBERG, ENDPOINT '{ICEBERG}', SECRET ice)")
    sql = f"""SELECT PULocationID, count(*) trips, sum(total_amount) revenue
              FROM wh.taxi.yellow WHERE pickup_month = '{FILTER_MONTH}' AND passenger_count > 3
              GROUP BY 1 ORDER BY revenue DESC LIMIT 5"""
    duck, duck_ms = timed(lambda: con.execute(sql).pl())
    emit(
        "iceberg_read",
        open_ms=open_ms,
        files_all=files_all,
        files_after_partition_prune=files_pruned,
        polars_ms=polars_ms,
        duckdb_ms=duck_ms,
        full_count=full.item(),
        full_count_ms=full_ms,
        same_answer=same(duck, result),
    )


def iceberg_concurrent() -> None:
    from pyiceberg.exceptions import CommitFailedException

    cat = catalog()
    before = len(cat.load_table("taxi.yellow").snapshots())
    sample = month_frame(months()[0]).head(1000).to_arrow()
    errors, retries = [], []

    def append():
        tries = 0
        while True:
            tries += 1
            try:
                catalog().load_table("taxi.yellow").append(sample)
                retries.append(tries)
                return
            except CommitFailedException:
                if tries >= 10:
                    errors.append("gave up after 10")
                    return
            except Exception as exc:
                errors.append(repr(exc)[:300])
                return

    threads = [threading.Thread(target=append) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    after = len(cat.load_table("taxi.yellow").snapshots())
    emit(
        "iceberg_concurrent",
        writers=4,
        snapshots_before=before,
        snapshots_after=after,
        attempts=retries,
        errors=errors,
    )


def iceberg_schema() -> None:
    from pyiceberg.types import StringType

    table = catalog().load_table("taxi.yellow")
    with table.update_schema() as update:
        update.add_column("note", StringType())
    extra = month_frame(months()[0]).head(10).with_columns(pl.lit("spike").alias("note")).to_arrow()
    table = catalog().load_table("taxi.yellow")
    table.append(extra.cast(table.schema().as_arrow()))
    emit("iceberg_schema", added_column="note" in table.schema().column_names)


def crash_writer(kind: str) -> None:
    """Child process: upload data files then die before committing."""
    frame = month_frame(months()[0]).head(200_000).to_arrow()
    if kind == "delta":
        from deltalake import write_deltalake

        # Kill on a timer while appends are in flight, so some die between data upload and commit.
        threading.Timer(0.8, lambda: os._exit(9)).start()
        for _ in range(50):
            write_deltalake(
                DELTA_URI,
                frame,
                mode="append",
                partition_by=["pickup_month"],
                storage_options=DELTA_OPTS,
            )
    else:
        table = catalog().load_table("taxi.yellow")
        threading.Timer(0.8, lambda: os._exit(9)).start()
        for _ in range(50):
            table.append(frame)


def crash(kind: str) -> None:
    import subprocess

    def readable():
        if kind == "delta":
            from deltalake import DeltaTable

            table = DeltaTable(DELTA_URI, storage_options=DELTA_OPTS)
            return table.version(), pl.scan_delta(DELTA_URI, storage_options=DELTA_OPTS).select(
                pl.len()
            ).collect().item()
        table = catalog().load_table("taxi.yellow")
        return table.current_snapshot().snapshot_id, pl.scan_iceberg(
            table, storage_options=ICEBERG_STORAGE
        ).select(pl.len()).collect().item()

    before = readable()
    child = subprocess.run(
        [sys.executable, __file__, f"crash_writer_{kind}"], env=os.environ, capture_output=True
    )
    after = readable()
    emit(
        f"{kind}_crash",
        child_exit=child.returncode,
        before=before,
        after=after,
        table_readable=True,
    )


def ingest_csv(kind: str) -> None:
    """Streaming CSV -> table for the large-file path: bounded memory regardless of file size."""
    csv = Path(os.environ.get("SPIKE_SCRATCH", HERE)) / "two_months.csv"
    if not csv.exists():
        pl.concat([pl.scan_parquet(p) for p in months()[:2]]).sink_csv(csv)
    started = time.perf_counter()
    scan = pl.scan_csv(csv, try_parse_dates=True).with_columns(pickup_month=pl.lit("csv"))
    target = "s3://delta/csv_ingest" if kind == "delta" else None
    if kind == "delta":
        from deltalake import write_deltalake

        rows = 0
        for batch in scan.collect_batches(chunk_size=1_000_000):
            rows += batch.height
            write_deltalake(target, batch.to_arrow(), mode="append", storage_options=DELTA_OPTS)
    else:
        cat = catalog()
        first = True
        rows = 0
        for batch in scan.collect_batches(chunk_size=1_000_000):
            arrow = batch.to_arrow()
            if first:
                table = cat.create_table("taxi.csv_ingest", schema=arrow.schema)
                first = False
            rows += arrow.num_rows
            table.append(arrow)
    emit(
        f"{kind}_csv_ingest",
        csv_mb=csv.stat().st_size // 2**20,
        rows=rows,
        seconds=round(time.perf_counter() - started, 1),
    )


STEPS = {
    "delta_write": delta_write,
    "delta_read": delta_read,
    "delta_concurrent": delta_concurrent,
    "delta_schema": delta_schema,
    "delta_optimize": delta_optimize,
    "delta_crash": lambda: crash("delta"),
    "delta_csv": lambda: ingest_csv("delta"),
    "iceberg_write": iceberg_write,
    "iceberg_read": iceberg_read,
    "iceberg_concurrent": iceberg_concurrent,
    "iceberg_schema": iceberg_schema,
    "iceberg_crash": lambda: crash("iceberg"),
    "iceberg_csv": lambda: ingest_csv("iceberg"),
    "crash_writer_delta": lambda: crash_writer("delta"),
    "crash_writer_iceberg": lambda: crash_writer("iceberg"),
}

if __name__ == "__main__":
    STEPS[sys.argv[1]]()
