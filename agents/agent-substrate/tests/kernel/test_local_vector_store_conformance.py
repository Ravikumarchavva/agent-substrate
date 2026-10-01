"""The kernel's own vector stores (in-memory and local filesystem), held to the conformance suite."""

from __future__ import annotations

import pytest

from substrate.kernel.storage.local_vector import LocalFilesystemVectorStore
from substrate.kernel.storage.vector import InMemoryVectorStore
from substrate.kernel.testing.conformance.vector_store import VectorStoreConformance


class TestInMemoryVectorStore(VectorStoreConformance):
    @pytest.fixture
    async def store(self):
        return InMemoryVectorStore()


class TestLocalFilesystemVectorStore(VectorStoreConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        return LocalFilesystemVectorStore(tmp_path)
