"""Durable storage backends (L2) — S3-compatible object storage + Postgres.

``WorkspaceFileStore`` lives in ``agents.storage.local_object_store`` — the
canonical zero-infra ``ObjectStore`` default. ``LocalFilesystemWorkspaceStore``
lives in ``agents.workspace``. This package holds backends with a real L2
dependency: ``S3Connector`` (raw S3-compatible client, SeaweedFS by default)
and ``S3FileStore`` (the ``ObjectStore`` Protocol implementation built on
top of it), Postgres-backed ``PgTaskStore`` and ``PostgresWorkspaceStore``.
"""

from substrate.integrations.storage.s3_connector import S3Connector
from substrate.integrations.storage.s3 import S3FileStore
from substrate.integrations.storage.pg_task_store import PgTaskStore

from substrate.integrations.storage.workspace_store import (
    BranchSnapshotHead,
    PostgresWorkspaceStore,
    SnapshotRecord,
    WorkspaceSnapshotBase,
)

__all__ = [
    "S3Connector",
    "S3FileStore",
    "PgTaskStore",
    "PostgresWorkspaceStore",
    "WorkspaceSnapshotBase",
    "SnapshotRecord",
    "BranchSnapshotHead",
]
