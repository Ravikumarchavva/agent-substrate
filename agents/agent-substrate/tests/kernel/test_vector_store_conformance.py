"""The store's vectors, held to the ``VectorStore`` conformance suite."""

from __future__ import annotations

import pytest

from substrate.stores import Store
from substrate.testing.conformance.vector_store import VectorStoreConformance


class TestVectors(VectorStoreConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        store = Store.at(tmp_path / "store")
        yield store.vectors
        await store.aclose()
