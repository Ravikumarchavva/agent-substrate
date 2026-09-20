"""Workspace snapshots and manifest authority protocols."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

from pydantic import Field, model_validator
from typing_extensions import Self

from substrate.kernel.core.content import JsonObject, KernelModel


class WorkspaceFileEntry(KernelModel):
    """File metadata stored in a workspace manifest."""

    path: str
    content_hash: str
    size_bytes: int
    metadata: JsonObject = Field(default_factory=dict)


class WorkspaceManifest(KernelModel):
    """Manifest mapping paths to file records in an isolated workspace snapshot."""

    files: dict[str, WorkspaceFileEntry] = Field(default_factory=dict)
    metadata: JsonObject = Field(default_factory=dict)


class WorkspaceSnapshot(KernelModel):
    """Point-in-time snapshot of an agent workspace on a specific branch.

    Invariant: manifest (inline) and manifest_ref (external CAS pointer)
    are strictly mutually exclusive — exactly one must be provided.
    """

    id: str = Field(default_factory=lambda: uuid4().hex)
    session_id: str
    branch_id: str
    parent_snapshot_id: str | None = None
    manifest: WorkspaceManifest | None = None
    manifest_ref: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def _validate_manifest_authority(self) -> Self:
        has_inline = self.manifest is not None
        has_ref = self.manifest_ref is not None
        if has_inline == has_ref:
            raise ValueError("Exactly one of manifest or manifest_ref must be provided")
        return self


class WorkspaceStore(Protocol):
    """Storage protocol for branch-isolated workspace snapshot trees."""

    async def get_snapshot(self, snapshot_id: str) -> WorkspaceSnapshot | None:
        """Fetch snapshot record by ID."""
        ...

    async def get_branch_snapshot_head(
        self, session_id: str, branch_id: str
    ) -> WorkspaceSnapshot | None:
        """Fetch current active snapshot head for a branch."""
        ...

    async def commit_snapshot(
        self,
        session_id: str,
        branch_id: str,
        new_snapshot: WorkspaceSnapshot,
        *,
        expected_parent_snapshot_id: str | None,
    ) -> WorkspaceSnapshot:
        """Atomically persist snapshot and advance branch snapshot head.

        Raises SnapshotConflictError if branch active snapshot != expected_parent_snapshot_id.
        """
        ...

    async def fork_branch_snapshot(
        self,
        session_id: str,
        source_branch_id: str,
        new_branch_id: str,
    ) -> WorkspaceSnapshot | None:
        """Initialize new branch snapshot head from source branch snapshot head."""
        ...

    async def list_snapshots(
        self, session_id: str, branch_id: str | None = None
    ) -> list[WorkspaceSnapshot]:
        """List snapshots for a session, optionally filtered by branch."""
        ...


__all__ = [
    "WorkspaceFileEntry",
    "WorkspaceManifest",
    "WorkspaceSnapshot",
    "WorkspaceStore",
]

