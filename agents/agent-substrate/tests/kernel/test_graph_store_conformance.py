"""The store's graph, held to the ``GraphStore`` conformance suite."""

from __future__ import annotations

import pytest

from substrate.stores import Store
from substrate.testing.conformance.graph_store import GraphStoreConformance


class TestGraph(GraphStoreConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        store = Store.at(tmp_path / "store")
        yield store.graph
        await store.aclose()
