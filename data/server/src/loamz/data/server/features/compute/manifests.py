"""Versioned job contracts between the control plane, the controller and workers."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from loamz.data.server.kernel.settings import Settings

Operation = Literal["infer", "ingest", "transform", "optimize", "materialize", "delete", "chunk"]
URI = r"^s3://[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]/[^\s]{1,1024}$"


class Partition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: str = Field(min_length=1, max_length=128)
    transform: Literal["identity", "year", "month", "day", "hour"] = "identity"


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def check(self, settings: Settings) -> None:
        """Defence in depth: a worker only touches the warehouse and the staging bucket."""


class _WritesTable(_Payload):
    location: str = Field(pattern=URI)
    write_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    partitioning: list[Partition] = Field(default_factory=list, max_length=8)

    def check(self, settings: Settings) -> None:
        if not self.location.startswith(f"s3://{settings.warehouse}/"):
            raise ValueError("Writes must target the warehouse")


def _staged(sources: list[str], settings: Settings) -> None:
    if not all(s.startswith(f"s3://{settings.staging_bucket}/") for s in sources):
        raise ValueError("Import sources must be staged uploads")


class InferPayload(_Payload):
    sources: list[str] = Field(min_length=1, max_length=10000)
    format: Literal["csv", "parquet", "ndjson"]

    def check(self, settings: Settings) -> None:
        _staged(self.sources, settings)


class IngestPayload(_WritesTable):
    sources: list[str] = Field(min_length=1, max_length=10000)
    format: Literal["csv", "parquet", "ndjson"]

    def check(self, settings: Settings) -> None:
        super().check(settings)
        _staged(self.sources, settings)


class TransformPayload(_WritesTable):
    source_files: list[str] = Field(max_length=100000)
    definition: dict

    def check(self, settings: Settings) -> None:
        super().check(settings)
        if not all(s.startswith(f"s3://{settings.warehouse}/") for s in self.source_files):
            raise ValueError("Transform sources must be warehouse files")


class OptimizePayload(_WritesTable):
    """Rewrite some of a table's files: fewer, larger and optionally ordered by `sort_by`."""

    source_files: list[str] = Field(min_length=1, max_length=200000)
    sort_by: list[str] = Field(default_factory=list, max_length=8)
    zorder: bool = False  # order by interleaving the sort_by columns instead of lexicographically
    bloom_columns: list[str] = Field(default_factory=list, max_length=8)
    target_file_bytes: int = Field(default=256 * 1024 * 1024, ge=16 * 2**20, le=2**31)
    row_group_rows: int = Field(default=1_000_000, ge=1000, le=10_000_000)

    def check(self, settings: Settings) -> None:
        super().check(settings)
        if not all(s.startswith(self.location.rstrip("/") + "/") for s in self.source_files):
            raise ValueError("Optimize reads only the table's own files")


class MaterializePayload(_WritesTable):
    """Run a signed `materialize` ticket and write its result into a table's location."""

    ticket: str = Field(min_length=20, max_length=8_000_000)
    schema_ipc: str = Field(max_length=200_000)  # base64 Arrow schema the table expects
    target_file_bytes: int = Field(default=1024 * 1024 * 1024, ge=16 * 2**20, le=2**33)


class DeletePayload(_Payload):
    """Remove data files nothing references any more. Refuses anything but Parquet under a table's
    data/ folder in the warehouse."""

    paths: list[str] = Field(min_length=1, max_length=50_000)

    def check(self, settings: Settings) -> None:
        root = f"s3://{settings.warehouse}/"
        for path in self.paths:
            if not (path.startswith(root) and "/data/" in path and path.endswith(".parquet")):
                raise ValueError("Only table data files in the warehouse can be deleted")
            if ".." in path.split("/"):
                raise ValueError("Path traversal is not allowed")


class ChunkPayload(_Payload):
    text: str = Field(min_length=1, max_length=2_000_000)


JOB_MODELS: dict[str, type[_Payload]] = {
    "infer": InferPayload,
    "ingest": IngestPayload,
    "transform": TransformPayload,
    "optimize": OptimizePayload,
    "materialize": MaterializePayload,
    "delete": DeletePayload,
    "chunk": ChunkPayload,
}
OPERATION_ROLE = {
    "infer": "jobs",
    "ingest": "jobs",
    "transform": "jobs",
    "optimize": "jobs",
    "materialize": "jobs",
    "delete": "jobs",
    "chunk": "documents",
}
DEFAULT_TIMEOUT = {
    "infer": 600,
    "ingest": 6 * 3600,
    "transform": 6 * 3600,
    "optimize": 6 * 3600,
    "materialize": 6 * 3600,
    "delete": 3600,
    "chunk": 300,
}


class JobEnvelope(BaseModel):
    """What the controller sends a worker for one execution attempt."""

    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    execution_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    operation: Operation
    storage_id: str = Field(max_length=64)
    timeout_seconds: float = Field(gt=0, le=24 * 3600)
    payload: dict


def storage_identity(settings: Settings) -> str:
    """What controller and workers compare to confirm they point at the same warehouse."""
    where = settings.storage_id or settings.s3_endpoint_url
    return hashlib.sha256(f"{where}|{settings.warehouse}".encode()).hexdigest()[:24]
