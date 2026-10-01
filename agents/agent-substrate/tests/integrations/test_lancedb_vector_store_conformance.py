"""The LanceDB vector store (local-path mode), held to the shared conformance suite."""

from __future__ import annotations

import pytest

pytest.importorskip("lancedb")

from substrate.integrations.vector.lancedb_store import LanceDBVectorStore  # noqa: E402
from substrate.kernel.testing.conformance.vector_store import VectorStoreConformance  # noqa: E402


class TestLanceDBVectorStore(VectorStoreConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        return LanceDBVectorStore(path=tmp_path / "lancedb")
