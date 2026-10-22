"""Vectors on both databases, held to the `VectorStore` conformance suite."""

from __future__ import annotations

import pytest

from loamz.engine.testing.conformance.vector_store import SearchableVectorStoreConformance


class TestVectors(SearchableVectorStoreConformance):
    @pytest.fixture
    async def store(self, storage):
        return storage.vectors
