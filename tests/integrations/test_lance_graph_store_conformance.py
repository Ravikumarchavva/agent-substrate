"""The Lance graph store (local-path mode), held to the shared conformance suite."""

from __future__ import annotations

import pytest

pytest.importorskip("lancedb")
pytest.importorskip("networkx")

from substrate.integrations.graph.lance_graph_store import LanceGraphStore  # noqa: E402
from substrate.testing.conformance.graph_store import GraphStoreConformance


class TestLanceGraphStore(GraphStoreConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        return LanceGraphStore(path=tmp_path / "graph")
