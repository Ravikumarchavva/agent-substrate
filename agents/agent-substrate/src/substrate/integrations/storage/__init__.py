"""Durable storage backends (L2) — S3-compatible object storage + Postgres.

The folder store's own files and workspaces are ``Store.files`` and ``substrate.workspace.Workspaces``.
This package holds backends with a real L2 dependency: ``S3Connector`` (raw S3-compatible client, SeaweedFS by default)
and ``S3FileStore`` (the ``FileStore`` Protocol implementation built on
top of it), and the Postgres-backed ``PostgresWorkspaceStore``.
"""

from substrate.integrations.storage.s3_connector import S3Connector
from substrate.integrations.storage.s3 import S3FileStore

from substrate.integrations.storage.workspace_store import (
    BranchSnapshotHead,
    PostgresWorkspaceStore,
    SnapshotRecord,
    WorkspaceSnapshotBase,
)

__all__ = [
    "S3Connector",
    "S3FileStore",
    "PostgresWorkspaceStore",
    "WorkspaceSnapshotBase",
    "SnapshotRecord",
    "BranchSnapshotHead",
]
