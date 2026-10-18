"""Tests for WorkspaceSnapshot, WorkspaceManifest, and the store's workspaces."""

import pytest
from pydantic import ValidationError

from substrate.workspace import (
    ContentRef,
    WorkspaceFileEntry,
    WorkspaceManifest,
    WorkspaceSnapshot,
)


class TestWorkspaceSnapshotValidation:
    def test_single_manifest_authority_inline(self) -> None:
        manifest = WorkspaceManifest(
            files={
                "main.py": WorkspaceFileEntry(
                    path="main.py",
                    content=ContentRef(hash="sha256-abc", size_bytes=42),
                )
            }
        )
        snap = WorkspaceSnapshot(
            session_id="s1",
            branch_id="main",
            manifest=manifest,
        )
        assert snap.manifest is not None
        assert snap.manifest_ref is None

    def test_single_manifest_authority_ref(self) -> None:
        snap = WorkspaceSnapshot(
            session_id="s1",
            branch_id="main",
            manifest_ref="cas://manifest-hash-123",
        )
        assert snap.manifest is None
        assert snap.manifest_ref == "cas://manifest-hash-123"

    def test_single_manifest_authority_both_rejected(self) -> None:
        manifest = WorkspaceManifest()
        with pytest.raises(
            ValidationError, match="Exactly one of manifest or manifest_ref"
        ):
            WorkspaceSnapshot(
                session_id="s1",
                branch_id="main",
                manifest=manifest,
                manifest_ref="cas://123",
            )

    def test_single_manifest_authority_neither_rejected(self) -> None:
        with pytest.raises(
            ValidationError, match="Exactly one of manifest or manifest_ref"
        ):
            WorkspaceSnapshot(
                session_id="s1",
                branch_id="main",
            )
