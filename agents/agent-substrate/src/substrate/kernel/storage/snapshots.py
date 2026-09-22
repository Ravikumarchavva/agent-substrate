"""Workspace snapshots and manifest authority protocols.

Content addressing follows the git/restic/Xet lineage: whole-file sha256
today, with a tagged ``ContentRef.kind`` as the one deliberate seam for
content-defined chunking later. Everything else CDC needs (a chunk list
type, a size threshold) can be added without touching any already-stored
manifest, because readers dispatch on ``kind`` — but only if no caller ever
assumes ``hash`` is a whole-file digest. It is, today; that assumption must
stay confined to the ``kind == "blob"`` branch.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Protocol, runtime_checkable
from uuid import uuid4

from pydantic import Field, model_validator
from typing_extensions import Self

from substrate.kernel.core.content import JsonObject, KernelModel


class ContentRef(KernelModel):
    """A reference to content in a blob CAS.

    ``kind`` is the content-defined-chunking escape hatch: v1 only ever
    produces ``"blob"`` (``hash`` = sha256 of the whole file). A future
    ``"chunked"`` kind would point ``hash`` at a chunk-list object instead —
    a new variant, not a format break, because every reader already
    dispatches on ``kind`` rather than assuming what ``hash`` addresses.
    """

    kind: Literal["blob", "chunked"] = "blob"
    hash: str
    size_bytes: int


class WorkspaceFileEntry(KernelModel):
    """File metadata stored in a workspace manifest."""

    path: str
    content: ContentRef
    mode: int = 0o644
    metadata: JsonObject = Field(default_factory=dict)


class WorkspaceManifest(KernelModel):
    """Manifest mapping paths to file records in an isolated workspace snapshot.

    ``files`` must be canonically ordered — insertion order sorted by path —
    whenever a manifest is constructed for hashing or diffing. This is what
    keeps two manifests with the same content producing the same
    serialization (dedup) and what keeps a future diff O(changed files)
    instead of O(all files): a linear merge of two sorted lists. Nothing
    enforces this at the type level today (a plain ``dict`` preserves
    insertion order, not sort order) — callers that build a manifest must
    sort ``files`` by key before constructing one.
    """

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


@runtime_checkable
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
        """Initialize new branch snapshot head from source branch's *current* head.

        For forking at an arbitrary historical point (not the source
        branch's current head — e.g. a conversation fork from an older
        message), use ``set_branch_snapshot_head`` instead with the
        specific snapshot id that point in history corresponds to.
        """
        ...

    async def set_branch_snapshot_head(
        self,
        session_id: str,
        branch_id: str,
        snapshot_id: str,
    ) -> WorkspaceSnapshot:
        """Point ``branch_id``'s head directly at an existing snapshot.

        Unlike ``commit_snapshot``, this creates no new snapshot and has no
        CAS check — it only ever targets a brand-new branch id (one with no
        existing head), the same "new branch, once" invariant
        ``fork_branch_snapshot`` and ``ensure_branch`` share. Raises
        ``ValueError`` if ``branch_id`` already has a head, or if
        ``snapshot_id`` doesn't exist.
        """
        ...

    async def list_snapshots(
        self, session_id: str, branch_id: str | None = None
    ) -> list[WorkspaceSnapshot]:
        """List snapshots for a session, optionally filtered by branch."""
        ...


__all__ = [
    "ContentRef",
    "WorkspaceFileEntry",
    "WorkspaceManifest",
    "WorkspaceSnapshot",
    "WorkspaceStore",
]

