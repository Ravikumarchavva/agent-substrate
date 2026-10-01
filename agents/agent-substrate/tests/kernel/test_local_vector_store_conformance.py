"""The kernel's local filesystem vector store, held to the conformance suite."""

from __future__ import annotations

import pytest

from substrate.kernel.storage.local_vector import LocalFilesystemVectorStore
from substrate.kernel.testing.conformance.vector_store import VectorStoreConformance


class TestLocalFilesystemVectorStore(VectorStoreConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        return LocalFilesystemVectorStore(tmp_path)
