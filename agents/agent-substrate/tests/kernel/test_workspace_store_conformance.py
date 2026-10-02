"""The store's workspace snapshots, held to the ``WorkspaceStore`` conformance suite."""

from __future__ import annotations

import pytest

from substrate.stores import Store
from substrate.testing.conformance.workspace_store import WorkspaceStoreConformance
from substrate.workspace import Workspaces


class TestWorkspaces(WorkspaceStoreConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        store = Store.at(tmp_path / "store")
        yield Workspaces(store)
        await store.aclose()
