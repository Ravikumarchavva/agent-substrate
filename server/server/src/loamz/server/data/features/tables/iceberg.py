"""Iceberg tables through the REST catalog. Control-plane only: reads metadata, never data.

Planning turns a statement's per-table filters into the exact list of data files a worker may
read. Commits register files that compute already wrote, in one atomic catalog operation.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import reduce
from typing import Any

import boto3
import pyarrow as pa
from botocore.config import Config as BotoConfig
from pyiceberg import expressions as ex
from pyiceberg.catalog import load_catalog
from pyiceberg.catalog.rest import RestCatalog
from pyiceberg.exceptions import NoSuchTableError
from pyiceberg.table import Table
from pyiceberg.transforms import (
    BucketTransform,
    DayTransform,
    HourTransform,
    IdentityTransform,
    MonthTransform,
    Transform,
    TruncateTransform,
    YearTransform,
)
from pyiceberg.types import (
    BinaryType,
    BooleanType,
    DateType,
    DecimalType,
    DoubleType,
    FixedType,
    FloatType,
    IcebergType,
    IntegerType,
    ListType,
    LongType,
    MapType,
    StringType,
    StructType,
    TimestampType,
    TimestamptzType,
    TimeType,
    UUIDType,
)

from loamz.engine.errors import ConflictError, InvalidRequestError
from loamz.server.data.kernel.container import Container
from loamz.server.data.kernel.errors import invalid
from loamz.server.data.kernel.settings import Settings

METADATA_TTL_SECONDS = 1.0
WRITE_ID = "loamz.write-id"
OPERATORS = {
    "eq": ex.EqualTo,
    "ne": ex.NotEqualTo,
    "gt": ex.GreaterThan,
    "ge": ex.GreaterThanOrEqual,
    "lt": ex.LessThan,
    "le": ex.LessThanOrEqual,
}


@dataclass(frozen=True)
class DataFile:
    path: str
    size: int
    rows: int


@dataclass(frozen=True)
class ScanPlan:
    snapshot_id: int | None
    files: tuple[DataFile, ...]
    columns: tuple[tuple[str, str], ...]
    total_files: int


def duckdb_type(kind: IcebergType) -> str:
    simple = {
        BooleanType: "BOOLEAN",
        IntegerType: "INTEGER",
        LongType: "BIGINT",
        FloatType: "FLOAT",
        DoubleType: "DOUBLE",
        DateType: "DATE",
        TimeType: "TIME",
        TimestampType: "TIMESTAMP",
        TimestamptzType: "TIMESTAMPTZ",
        StringType: "VARCHAR",
        UUIDType: "UUID",
        BinaryType: "BLOB",
        FixedType: "BLOB",
    }
    for cls, name in simple.items():
        if isinstance(kind, cls):
            return name
    if isinstance(kind, DecimalType):
        return f"DECIMAL({kind.precision},{kind.scale})"
    if isinstance(kind, ListType):
        return duckdb_type(kind.element_type) + "[]"
    if isinstance(kind, MapType):
        return f"MAP({duckdb_type(kind.key_type)}, {duckdb_type(kind.value_type)})"
    if isinstance(kind, StructType):
        inner = ", ".join(f'"{f.name}" {duckdb_type(f.field_type)}' for f in kind.fields)
        return f"STRUCT({inner})"
    return "VARCHAR"


def coerce(value: Any, kind: IcebergType) -> Any:
    """SQL literals as Iceberg expects them: '2026-08-01' or '2026-08-01 10:00' for timestamps."""
    if isinstance(value, str) and isinstance(kind, (TimestampType, TimestamptzType, DateType)):
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
        if isinstance(kind, DateType):
            return parsed.date()
        if isinstance(kind, TimestamptzType) and parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        if isinstance(kind, TimestampType) and parsed.tzinfo is not None:
            return None
        return parsed
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return value
    if isinstance(kind, (IntegerType, LongType)) and isinstance(value, float):
        return None if not value.is_integer() else int(value)
    return value


def parse_transform(text: str) -> Transform:
    text = text.strip().lower()
    fixed = {
        "identity": IdentityTransform(),
        "year": YearTransform(),
        "month": MonthTransform(),
        "day": DayTransform(),
        "hour": HourTransform(),
    }
    if text in fixed:
        return fixed[text]
    if match := re.fullmatch(r"(bucket|truncate)\[(\d{1,5})\]", text):
        size = int(match.group(2))
        return BucketTransform(size) if match.group(1) == "bucket" else TruncateTransform(size)
    raise invalid(f"Unsupported partition transform {text!r}")


class TableService:
    def __init__(self, settings: Settings, catalog: RestCatalog | None = None):
        self.settings = settings
        key = settings.s3_access_key_id.get_secret_value()
        secret = settings.s3_secret_access_key.get_secret_value()
        self.catalog = catalog or load_catalog(
            "loamz",
            **{
                "type": "rest",
                "uri": settings.iceberg_catalog_url,
                "oauth2-server-uri": settings.iceberg_catalog_url + "/v1/oauth/tokens",
                "warehouse": f"s3://{settings.warehouse}/",
                "credential": f"{key}:{secret}",
                "s3.endpoint": settings.s3_endpoint_url,
                "s3.access-key-id": key,
                "s3.secret-access-key": secret,
                "s3.region": settings.s3_region,
            },
        )
        # Listing only (never object contents): used to find files no table references.
        self.s3 = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=key,
            aws_secret_access_key=secret,
            region_name=settings.s3_region,
            config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        self.lock = threading.Lock()
        self.tables: dict[tuple[str, ...], tuple[float, Table]] = {}
        self.plans: dict[tuple, ScanPlan] = {}

    @staticmethod
    def identifier(record: dict[str, Any]) -> tuple[str, str, str]:
        return (record["catalog_name"], record["schema_name"], record["name"])

    # ------------------------------------------------------------------ metadata
    def load(self, record: dict[str, Any], fresh: bool = False) -> Table:
        identifier = self.identifier(record)
        with self.lock:
            cached = self.tables.get(identifier)
        if cached and not fresh and time.monotonic() - cached[0] < METADATA_TTL_SECONDS:
            return cached[1]
        try:
            table = self.catalog.load_table(identifier)
        except NoSuchTableError:
            raise ConflictError("The table's Iceberg metadata is missing") from None
        with self.lock:
            self.tables[identifier] = (time.monotonic(), table)
        return table

    def invalidate(self, record: dict[str, Any]) -> None:
        with self.lock:
            self.tables.pop(self.identifier(record), None)

    def create(
        self, record: dict[str, Any], schema: pa.Schema, partitioning: list[dict[str, str]]
    ) -> Table:
        namespace = (record["catalog_name"], record["schema_name"])
        self.catalog.create_namespace_if_not_exists(namespace)
        table = self.catalog.create_table(self.identifier(record), schema=schema)
        if partitioning:
            with table.update_spec() as spec:
                for item in partitioning:
                    transform = parse_transform(item.get("transform", "identity"))
                    field = table.schema().find_field(item["column"], case_sensitive=False)
                    spec.add_field(
                        field.name, transform, f"{field.name}_{type(transform).__name__.lower()}"
                    )
        return table

    def drop(self, record: dict[str, Any]) -> None:
        try:
            self.catalog.purge_table(self.identifier(record))
        except NoSuchTableError:
            pass
        self.invalidate(record)

    @staticmethod
    def columns(table: Table) -> tuple[tuple[str, str], ...]:
        return tuple((f.name, duckdb_type(f.field_type)) for f in table.schema().fields)

    @staticmethod
    def describe(table: Table) -> dict[str, Any]:
        snapshot = table.current_snapshot()
        summary = snapshot.summary.additional_properties if snapshot and snapshot.summary else {}
        return {
            "snapshot_id": str(snapshot.snapshot_id) if snapshot else None,
            "row_count": int(summary.get("total-records", 0)),
            "size_bytes": int(summary.get("total-files-size", 0)),
            "file_count": int(summary.get("total-data-files", 0)),
            "columns": [
                {"name": f.name, "type": duckdb_type(f.field_type), "nullable": not f.required}
                for f in table.schema().fields
            ],
            "partitioning": [
                {
                    "column": table.schema().find_field(p.source_id).name,
                    "transform": str(p.transform),
                }
                for p in table.spec().fields
            ],
        }

    # ------------------------------------------------------------------ planning
    def plan(self, record: dict[str, Any], filters: list[tuple[str, str, Any]]) -> ScanPlan:
        table = self.load(record)
        snapshot = table.current_snapshot()
        schema = table.schema()
        fields = {f.name.lower(): f for f in schema.fields}
        predicates = []
        for column, operator, value in filters:
            field = fields.get(column.lower())
            if field is None:
                continue
            values = [coerce(v, field.field_type) for v in (value if operator == "in" else [value])]
            if any(v is None for v in values):
                continue
            predicate = (
                ex.In(field.name, tuple(values))
                if operator == "in"
                else OPERATORS[operator](field.name, values[0])
            )
            try:
                predicate.bind(schema)
            except ValueError, TypeError:
                continue  # this literal cannot prune; the others still do
            predicates.append(predicate)
        key = (
            self.identifier(record),
            snapshot.snapshot_id if snapshot else None,
            repr(predicates),
        )
        with self.lock:
            cached = self.plans.get(key)
        if cached:
            return cached
        columns = self.columns(table)
        if snapshot is None:
            plan = ScanPlan(None, (), columns, 0)
        else:
            row_filter = reduce(ex.And, predicates) if predicates else ex.AlwaysTrue()
            tasks = list(table.scan(row_filter=row_filter, case_sensitive=False).plan_files())
            if any(task.delete_files for task in tasks):
                raise InvalidRequestError("Tables with row-level deletes are not supported yet")
            total = int(
                (snapshot.summary.additional_properties if snapshot.summary else {}).get(
                    "total-data-files", len(tasks)
                )
            )
            plan = ScanPlan(
                snapshot.snapshot_id,
                tuple(
                    DataFile(t.file.file_path, t.file.file_size_in_bytes, t.file.record_count)
                    for t in tasks
                ),
                columns,
                total,
            )
        with self.lock:
            if len(self.plans) > 4096:
                self.plans.clear()
            self.plans[key] = plan
        return plan

    # ------------------------------------------------------------------ commits
    def commit_files(
        self,
        record: dict[str, Any],
        paths: list[str],
        mode: str,
        write_id: str,
        schema: pa.Schema | None = None,
    ) -> Table:
        """Register already-written data files in one atomic, idempotent commit.

        The write id is stored on the snapshot; replaying a publication after a crash finds it
        and does not register the same files twice.
        """
        table = self.load(record, fresh=True)
        if any(
            snapshot.summary and snapshot.summary.additional_properties.get(WRITE_ID) == write_id
            for snapshot in table.snapshots()
        ):
            return table
        if schema is not None:
            existing = {f.name.lower() for f in table.schema().fields}
            missing = [f for f in schema if f.name.lower() not in existing]
            if missing:
                with table.update_schema() as update:
                    update.union_by_name(pa.schema(missing))
                table = self.load(record, fresh=True)
        properties = {WRITE_ID: write_id}
        with table.transaction() as transaction:
            if mode == "overwrite" and table.current_snapshot() is not None:
                transaction.delete(ex.AlwaysTrue(), snapshot_properties=properties)
            if paths:
                transaction.add_files(
                    paths, snapshot_properties=properties, check_duplicate_files=False
                )
        self.invalidate(record)
        return self.load(record, fresh=True)

    def replace_files(
        self, record: dict[str, Any], old_paths: list[str], new_paths: list[str], write_id: str
    ) -> Table:
        """Swap some data files for others holding the same rows, in one atomic commit.

        Only the named files are removed, so rows appended while a rewrite ran are kept. If any
        named file is no longer in the table (another rewrite or an overwrite got there first) the
        swap is refused instead of resurrecting or losing data. Like `commit_files`, the write id
        makes a replayed commit a no-op.
        """
        # The helper PyIceberg's own add_files uses; there is no public equivalent for file-level swaps.
        from pyiceberg.table import _parquet_files_to_data_files

        table = self.load(record, fresh=True)
        if any(
            snapshot.summary and snapshot.summary.additional_properties.get(WRITE_ID) == write_id
            for snapshot in table.snapshots()
        ):
            return table
        current = {task.file.file_path: task.file for task in table.scan().plan_files()}
        gone = [path for path in old_paths if path not in current]
        if gone:
            raise ConflictError(
                f"The table changed while it was being rewritten ({len(gone)} files are gone); "
                "run the operation again"
            )
        properties = {WRITE_ID: write_id}
        with table.transaction() as transaction:
            if transaction.table_metadata.name_mapping() is None:
                transaction.set_properties(
                    **{
                        "schema.name-mapping.default": transaction.table_metadata.schema().name_mapping.model_dump_json()
                    }
                )
            with transaction.update_snapshot(snapshot_properties=properties).overwrite() as swap:
                for path in old_paths:
                    swap.delete_data_file(current[path])
                for data_file in _parquet_files_to_data_files(
                    table_metadata=transaction.table_metadata, file_paths=new_paths, io=table.io
                ):
                    swap.append_data_file(data_file)
        self.invalidate(record)
        return self.load(record, fresh=True)

    # ------------------------------------------------------------------ garbage collection
    def referenced_files(self, table: Table) -> set[str]:
        """Data files any snapshot of the table still points at (metadata only)."""
        if table.current_snapshot() is None:
            return set()
        return set(table.inspect.all_data_files().column("file_path").to_pylist())

    def expire_snapshots(
        self, record: dict[str, Any], older_than: datetime, retain_last: int
    ) -> tuple[int, list[str]]:
        """Drop old snapshots (never the current one, never the newest `retain_last`) and return
        how many were dropped and which data files nothing references any more."""
        table = self.load(record, fresh=True)
        ordered = sorted(table.snapshots(), key=lambda snap: snap.timestamp_ms, reverse=True)
        keep = {snap.snapshot_id for snap in ordered[:retain_last]}
        current = table.current_snapshot()
        if current is not None:
            keep.add(current.snapshot_id)
        cutoff = int(older_than.timestamp() * 1000)
        doomed = [
            snap.snapshot_id
            for snap in ordered
            if snap.snapshot_id not in keep and snap.timestamp_ms < cutoff
        ]
        if not doomed:
            return 0, []
        before = self.referenced_files(table)
        table.maintenance.expire_snapshots().by_ids(doomed).commit()
        self.invalidate(record)
        after = self.referenced_files(self.load(record, fresh=True))
        return len(doomed), sorted(before - after)

    def orphan_files(
        self, record: dict[str, Any], older_than: datetime, limit: int = 50_000
    ) -> list[str]:
        """Data files in the table's folder that no snapshot references and that are old enough that
        no write can still be about to commit them."""
        table = self.load(record, fresh=True)
        referenced = self.referenced_files(table)
        location = table.location().rstrip("/") + "/data/"
        bucket, _, prefix = location.removeprefix("s3://").partition("/")
        found: list[str] = []
        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                path = f"s3://{bucket}/{item['Key']}"
                if (
                    item["LastModified"] < older_than
                    and path not in referenced
                    and item["Key"].endswith(".parquet")
                ):
                    found.append(path)
                    if len(found) >= limit:
                        return found
        return found

    def appended_since(self, record: dict[str, Any], since: str | None) -> list[str] | None:
        """Data files added after snapshot `since`, but only if every snapshot since then was a plain
        append. Any overwrite, delete or rewrite in between (or an expired `since`) returns None:
        the caller must then recompute from scratch, because "new files" no longer means "new rows"."""
        table = self.load(record, fresh=True)
        current = table.current_snapshot()
        if current is None or since is None:
            return None
        if str(current.snapshot_id) == str(since):
            return []
        lineage = []
        snapshot = current
        while snapshot is not None and str(snapshot.snapshot_id) != str(since):
            lineage.append(snapshot)
            snapshot = (
                table.snapshot_by_id(snapshot.parent_snapshot_id)
                if snapshot.parent_snapshot_id
                else None
            )
        if snapshot is None:
            return None  # the starting snapshot is not an ancestor (or was expired)
        if any(not snap.summary or snap.summary.operation.value != "append" for snap in lineage):
            return None
        scan = table.incremental_append_scan(
            from_snapshot_id_exclusive=int(since), to_snapshot_id_inclusive=current.snapshot_id
        )
        return sorted(task.file.file_path for task in scan.plan_files())


def table_service(container: Container) -> TableService:
    """Provider: the Iceberg tables of the warehouse."""
    return TableService(container.settings)
