"""Durable file storage backends (L2).

``LocalFilesystemWorkspaceStore`` moved to ``agents.workspace`` — pure
kernel+stdlib, no durable-infra dependency, so it belongs at L1 beside the
rest of the workspace package. This package now holds only backends with a
real L2 dependency (S3-compatible object storage, Postgres).
"""

from substrate.capabilities.storage.s3 import S3FileStore
from substrate.capabilities.storage.workspace import (
    WorkspaceFileStore,
    WorkspacePathError,
    WorkspaceQuotaExceededError,
)

from substrate.capabilities.storage.workspace_store import (
    BranchSnapshotHead,
    PostgresWorkspaceStore,
    SnapshotRecord,
    WorkspaceSnapshotBase,
)

__all__ = [
    "S3FileStore",
    "WorkspaceFileStore",
    "WorkspacePathError",
    "WorkspaceQuotaExceededError",
    "PostgresWorkspaceStore",
    "WorkspaceSnapshotBase",
    "SnapshotRecord",
    "BranchSnapshotHead",
]

