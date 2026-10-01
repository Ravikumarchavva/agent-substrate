"""The kernel's graph store (local filesystem), held to the conformance suite."""

from __future__ import annotations

import pytest

from substrate.kernel.storage.local_graph import LocalFilesystemGraphStore
from substrate.kernel.testing.conformance.graph_store import GraphStoreConformance


class TestLocalFilesystemGraphStore(GraphStoreConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        return LocalFilesystemGraphStore(tmp_path)
