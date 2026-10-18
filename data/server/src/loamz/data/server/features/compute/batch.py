"""Batch work executed by attached compute: ingest and transform.

Both stream with Polars from object storage straight into the target table's location, one file
set per partition, and return the written files. The control plane commits them to Iceberg.
Memory stays bounded by the streaming engine regardless of input size.
"""

from __future__ import annotations

import base64
import posixpath
import re
from typing import Any
from uuid import uuid4

import polars as pl
import pyarrow as pa
import pyarrow.fs as pafs

from loamz.data.server.kernel.settings import Settings

TRANSFORMS = {"year": "1y", "month": "1mo", "day": "1d", "hour": "1h"}
TARGET_FILE_BYTES = 256 * 1024 * 1024
ROW_GROUP_ROWS = 1_000_000


def storage_options(settings: Settings) -> dict[str, str]:
    return {
        "aws_access_key_id": settings.s3_access_key_id.get_secret_value(),
        "aws_secret_access_key": settings.s3_secret_access_key.get_secret_value(),
        "aws_endpoint_url": settings.s3_endpoint_url,
        "aws_region": settings.s3_region,
        "aws_allow_http": str(settings.s3_endpoint_url.startswith("http://")).lower(),
    }


def filesystem(settings: Settings) -> pafs.S3FileSystem:
    endpoint = settings.s3_endpoint_url
    scheme, _, host = endpoint.partition("://")
    return pafs.S3FileSystem(
        access_key=settings.s3_access_key_id.get_secret_value(),
        secret_key=settings.s3_secret_access_key.get_secret_value(),
        endpoint_override=host,
        scheme=scheme,
        region=settings.s3_region,
    )


def scan_source(uris: list[str], file_format: str, settings: Settings) -> pl.LazyFrame:
    """One lazy scan over every file; schemas that drift between files are merged by name."""
    options = storage_options(settings)

    def scan(uri: str) -> pl.LazyFrame:
        if file_format == "csv":
            return pl.scan_csv(
                uri, storage_options=options, try_parse_dates=True, infer_schema_length=100_000
            )
        if file_format == "parquet":
            return pl.scan_parquet(uri, storage_options=options, hive_partitioning=False)
        if file_format == "ndjson":
            return pl.scan_ndjson(uri, storage_options=options, infer_schema_length=100_000)
        raise ValueError("Supported import formats: csv, parquet, ndjson")

    frames = [scan(uri) for uri in uris]
    return frames[0] if len(frames) == 1 else pl.concat(frames, how="diagonal_relaxed")


def normalize(frame: pl.LazyFrame) -> pl.LazyFrame:
    """Coerce types Iceberg v2 cannot store: nanosecond timestamps become microseconds."""
    schema = frame.collect_schema()
    casts = [
        pl.col(name).dt.cast_time_unit("us")
        for name, dtype in schema.items()
        if isinstance(dtype, pl.Datetime) and dtype.time_unit == "ns"
    ]
    nulls = [pl.col(n).cast(pl.String) for n, d in schema.items() if d == pl.Null]
    return frame.with_columns(casts + nulls) if casts or nulls else frame


def partition_keys(partitioning: list[dict[str, str]]) -> dict[str, pl.Expr]:
    keys = {}
    for index, item in enumerate(partitioning):
        column, transform = item["column"], item.get("transform", "identity")
        if transform == "identity":
            keys[f"__p{index}"] = pl.col(column)
        elif transform in TRANSFORMS:
            keys[f"__p{index}"] = pl.col(column).dt.truncate(TRANSFORMS[transform])
        else:
            raise ValueError(f"Partition transform {transform} is not supported for writes")
    return keys


def write_files(
    frame: pl.LazyFrame,
    location: str,
    write_id: str,
    partitioning: list[dict[str, str]],
    settings: Settings,
    target_file_bytes: int = TARGET_FILE_BYTES,
    row_group_rows: int = ROW_GROUP_ROWS,
) -> list[dict[str, Any]]:
    """Stream the frame into `<table location>/data/<write_id>/`, one file set per partition."""
    # A fresh directory per attempt: a retried job never overwrites files an earlier attempt wrote.
    base = location.rstrip("/") + f"/data/{write_id}/{uuid4().hex}/"
    keys = partition_keys(partitioning)
    written: list[dict[str, Any]] = []

    def record(args: Any) -> None:
        # Sizes and row counts come from the writer: no second read of the files.
        written.extend(
            {
                "path": p.path if "://" in p.path else "s3://" + p.path,
                "size": p.num_bytes,
                "rows": p.num_rows,
            }
            for p in args.paths
        )

    frame.sink_parquet(
        pl.PartitionBy(
            base,
            key=keys or None,
            include_key=False if keys else None,
            approximate_bytes_per_file=target_file_bytes,
        ),
        storage_options=storage_options(settings),
        row_group_size=row_group_rows,
        statistics=True,
        mkdir=True,
        sinked_paths_callback=record,
    )
    return [f for f in written if f["rows"]]


def arrow_schema(frame: pl.LazyFrame) -> str:
    """The frame's schema as base64 Arrow IPC, without materializing any rows."""
    schema = pl.DataFrame(schema=frame.collect_schema()).to_arrow().schema
    return base64.b64encode(schema.serialize().to_pybytes()).decode()


def infer(payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Schema of the staged input after normalization (reads a bounded sample)."""
    frame = normalize(scan_source(payload["sources"], payload["format"], settings))
    return {"schema": arrow_schema(frame)}


def ingest(payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Stream staged input into the table's location: normalized, partitioned, written once."""
    frame = normalize(scan_source(payload["sources"], payload["format"], settings))
    schema = arrow_schema(frame)  # resolved once from the plan; no extra read of the data
    files = write_files(
        frame, payload["location"], payload["write_id"], payload["partitioning"], settings
    )
    return {"files": files, "rows": sum(f["rows"] for f in files), "schema": schema}


def transform(payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Declarative pipeline: project, filter, de-duplicate; reads only the planned source files."""
    definition = payload["definition"]
    source = payload["source_files"]
    if source:
        frame = pl.scan_parquet(
            source,
            storage_options=storage_options(settings),
            missing_columns="insert",
            hive_partitioning=False,  # folder names are layout, not columns
        )
    else:
        return {"files": [], "rows": 0}
    if definition.get("filter_column"):
        column = pl.col(definition["filter_column"])
        dtype = frame.collect_schema()[definition["filter_column"]]
        value = pl.lit(definition.get("value")).cast(dtype)
        operator = definition.get("operator", "eq")
        frame = frame.filter(
            column == value
            if operator == "eq"
            else column > value
            if operator == "gt"
            else column < value
        )
    if definition.get("columns"):
        frame = frame.select(definition["columns"])
    if definition.get("drop_nulls"):
        frame = frame.drop_nulls()
    if definition.get("unique"):
        frame = frame.unique()
    files = write_files(
        normalize(frame),
        payload["location"],
        payload["write_id"],
        payload["partitioning"],
        settings,
    )
    return {"files": files, "rows": sum(f["rows"] for f in files)}


Z_BITS = 16


def zorder_key(frame: pl.LazyFrame, columns: list[str]) -> pl.Expr:
    """A Z-order (Morton) key: each column is turned into its rank scaled to 16 bits and the bits
    are interleaved, so rows close in *every* column are close in the key. Sorting by it keeps
    the min/max range of each file narrow in all of the columns at once, where an ordinary sort
    does that for the first column only."""
    count = frame.select(pl.len()).collect().item()
    scale = max(count - 1, 1)
    width = len(columns)
    scaled = [
        ((pl.col(c).rank("ordinal") - 1).cast(pl.UInt64) * ((1 << Z_BITS) - 1) // scale)
        for c in columns
    ]
    terms = [
        ((value // (1 << bit)) % 2) * (1 << (bit * width + index))
        for index, value in enumerate(scaled)
        for bit in range(Z_BITS)
    ]
    return pl.sum_horizontal(terms).alias("__z")


class PartitionedWriter:
    """Streams frames into Parquet files through PyArrow, one open file per partition.

    PyArrow (unlike Polars' sink) can write bloom filters. A file is closed when it has taken roughly
    `target_file_bytes` once compressed (estimated at a quarter of the in-memory size).
    """

    MAX_OPEN = 64

    def __init__(
        self,
        settings: Settings,
        location: str,
        write_id: str,
        partitioning: list[dict[str, str]],
        schema: pa.Schema,
        target_file_bytes: int,
        row_group_rows: int = ROW_GROUP_ROWS,
        bloom_columns: list[str] | None = None,
    ):
        import pyarrow.parquet as pq

        self.pq = pq
        self.fs = filesystem(settings)
        self.base = location.rstrip("/") + f"/data/{write_id}/{uuid4().hex}/"
        self.keys = partition_keys(partitioning)
        self.schema, self.target, self.row_group_rows = schema, target_file_bytes, row_group_rows
        missing = set(bloom_columns or []) - set(schema.names)
        if missing:
            raise ValueError(f"Cannot build bloom filters on unknown columns: {sorted(missing)}")
        self.bloom = {name: True for name in bloom_columns or []}
        self.written: list[dict[str, Any]] = []
        self.open: dict[tuple, dict[str, Any]] = {}
        self.counter = 0

    def _close(self, entry: dict[str, Any]) -> None:
        entry["writer"].close()
        info = self.fs.get_file_info(entry["path"].removeprefix("s3://"))
        self.written.append({"path": entry["path"], "size": info.size, "rows": entry["rows"]})

    def _open(self, key: tuple) -> dict[str, Any]:
        self.counter += 1
        # Only safe characters in object keys: partition values are timestamps, strings, anything.
        label = "_".join(re.sub(r"[^A-Za-z0-9._=-]", "-", str(part)) for part in key) or "all"
        path = f"{self.base}{label}/part-{self.counter:06d}.parquet"
        writer = self.pq.ParquetWriter(
            path.removeprefix("s3://"),
            self.schema,
            filesystem=self.fs,
            compression="zstd",
            write_statistics=True,
            **({"bloom_filter_options": self.bloom} if self.bloom else {}),
        )
        return {"writer": writer, "path": path, "rows": 0, "bytes": 0}

    def write(self, frame: pl.DataFrame) -> None:
        parts = (
            frame.with_columns(**self.keys).partition_by(
                list(self.keys), as_dict=True, include_key=False
            )
            if self.keys
            else {(): frame}
        )
        for key, part in parts.items():
            if key not in self.open and len(self.open) >= self.MAX_OPEN:
                raise ValueError(f"More than {self.MAX_OPEN} partitions are open at once")
            entry = self.open.get(key) or self.open.setdefault(key, self._open(key))
            table = part.to_arrow().cast(self.schema)
            entry["writer"].write_table(table, row_group_size=self.row_group_rows)
            entry["rows"] += table.num_rows
            entry["bytes"] += table.nbytes
            if entry["bytes"] / 4 >= self.target:
                self._close(self.open.pop(key))

    def finish(self) -> list[dict[str, Any]]:
        for entry in self.open.values():
            self._close(entry)
        self.open.clear()
        return [f for f in self.written if f["rows"]]


def write_with_bloom(
    frame: pl.LazyFrame,
    location: str,
    write_id: str,
    partitioning: list[dict[str, str]],
    settings: Settings,
    target_file_bytes: int,
    bloom_columns: list[str],
    row_group_rows: int = ROW_GROUP_ROWS,
) -> list[dict[str, Any]]:
    """Like `write_files`, but through PyArrow so Parquet bloom filters can be written.

    A bloom filter lets DuckDB skip a row group for an equality filter (`WHERE id = 42`) on a
    high-cardinality column that min/max statistics cannot narrow.
    """
    schema = pl.DataFrame(schema=frame.collect_schema()).to_arrow().schema
    writer = PartitionedWriter(
        settings,
        location,
        write_id,
        partitioning,
        schema,
        target_file_bytes,
        row_group_rows,
        bloom_columns,
    )
    for chunk in frame.collect_batches(chunk_size=row_group_rows):
        writer.write(chunk)
    return writer.finish()


def optimize(payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Rewrite files into fewer, larger ones, optionally ordered and with bloom filters.

    Ordering by the columns queries filter on tightens each file's min/max statistics, so the
    planner can skip more files and DuckDB more row groups. `zorder` does it for several columns at
    once. Nothing here changes the table: the control plane swaps the old files for the returned
    ones in one atomic commit.
    """
    frame = pl.scan_parquet(
        payload["source_files"],
        storage_options=storage_options(settings),
        missing_columns="insert",
        hive_partitioning=False,
    )
    names = set(frame.collect_schema().names())
    unknown = (set(payload["sort_by"]) | set(payload.get("bloom_columns", []))) - names
    if unknown:
        raise ValueError(f"Unknown columns: {sorted(unknown)}")
    if payload.get("zorder"):
        frame = (
            frame.with_columns(zorder_key(frame, payload["sort_by"]))
            .sort("__z", maintain_order=True)
            .drop("__z")
        )
    elif payload["sort_by"]:
        frame = frame.sort(payload["sort_by"], nulls_last=True, maintain_order=True)
    frame = normalize(frame)
    bloom = payload.get("bloom_columns") or []
    if bloom:
        files = write_with_bloom(
            frame,
            payload["location"],
            payload["write_id"],
            payload["partitioning"],
            settings,
            payload["target_file_bytes"],
            bloom,
            payload.get("row_group_rows", ROW_GROUP_ROWS),
        )
    else:
        files = write_files(
            frame,
            payload["location"],
            payload["write_id"],
            payload["partitioning"],
            settings,
            payload["target_file_bytes"],
            payload.get("row_group_rows", ROW_GROUP_ROWS),
        )
    return {
        "files": files,
        "rows": sum(f["rows"] for f in files),
        "bytes": sum(f["size"] for f in files),
    }


def materialize(payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Run a signed query on a locked-down DuckDB and stream its result into Parquet files.

    The control plane planned and authorized the query (as the view's definer) and signed it, so
    this worker reads exactly the files the ticket names. Batches go straight from DuckDB to
    object storage, split by the table's partitioning; memory stays bounded by the batch size,
    not the result size.
    """
    from loamz.data.server.features.compute.worker import StatementEngine
    from loamz.data.server.kernel.tickets import TicketVerifier

    claims = TicketVerifier(settings.ticket_public_key_file).verify(payload["ticket"])
    if claims.get("op") != "materialize":
        raise ValueError("Ticket does not authorize a materialization")
    writer = PartitionedWriter(
        settings,
        payload["location"],
        payload["write_id"],
        payload["partitioning"],
        pa_schema(payload["schema_ipc"]),
        payload["target_file_bytes"],
    )
    for batch in StatementEngine(settings).batches(claims):
        writer.write(pl.from_arrow(batch))
    files = writer.finish()
    return {"files": files, "rows": sum(f["rows"] for f in files)}


def delete(payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Delete files, ignoring ones already gone; returns how many were removed."""
    fs = filesystem(settings)
    removed = 0
    for path in payload["paths"]:
        target = path.removeprefix("s3://")
        if fs.get_file_info(target).type == pafs.FileType.NotFound:
            continue
        fs.delete_file(target)
        removed += 1
    return {"removed": removed, "requested": len(payload["paths"])}


def pa_schema(encoded: str) -> pa.Schema:
    return pa.ipc.read_schema(pa.py_buffer(base64.b64decode(encoded)))


def staged_name(key: str) -> str:
    return posixpath.basename(key)
