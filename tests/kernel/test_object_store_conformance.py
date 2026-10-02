"""The kernel's object store (local workspace directory), held to the conformance suite."""

from __future__ import annotations

import pytest

from substrate.stores import WorkspaceFileStore
from substrate.testing.conformance.object_store import ObjectStoreConformance


class TestWorkspaceFileStore(ObjectStoreConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        return WorkspaceFileStore(tmp_path, user_quota_bytes=10**9)
