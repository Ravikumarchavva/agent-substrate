"""The kernel's local filesystem vector store, held to the conformance suite."""

from __future__ import annotations

import pytest

from substrate.stores import LocalFilesystemVectorStore
from substrate.testing.conformance.vector_store import VectorStoreConformance


class TestLocalFilesystemVectorStore(VectorStoreConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        return LocalFilesystemVectorStore(tmp_path)
