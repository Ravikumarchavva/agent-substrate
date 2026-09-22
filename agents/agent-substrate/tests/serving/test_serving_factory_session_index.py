"""build_session_index_vector_store — per-(tenant, user) Lance factory.

A per-user store, not a single shared instance the way PgVectorStore is:
a Lance Namespace table identifier needs tenant_id/user_id baked into its
namespace_path, which isn't known until a real request exists. These tests
pin the two connection modes (local-dev path, remote namespace catalog) and
that tenant_id/user_id are validated the same way every other object-storage
key is (layout.py's `_id()`), not accepted as raw path/namespace segments.
"""

from __future__ import annotations

import pytest

from substrate.integrations.graph.lance_graph_store import LanceGraphStore
from substrate.integrations.memory.lance_memory_store import LanceMemoryStore
from substrate.integrations.vector.lancedb_store import LanceDBVectorStore
from substrate.config import SubstrateConfig
from substrate.serving.factory import (
    build_page_index_memory,
    build_session_graph_store,
    build_session_index_vector_store,
)


def test_local_mode_scopes_path_by_tenant_and_user(tmp_path) -> None:
    cfg = SubstrateConfig(SESSION_INDEX_LOCAL_PATH=str(tmp_path))
    store = build_session_index_vector_store(cfg, "tenant-a", "user-a")

    assert isinstance(store, LanceDBVectorStore)
    assert store._namespace_uri is None
    assert store._path == str(tmp_path / "tenants/tenant-a/users/user-a/index")


def test_namespace_mode_scopes_path_by_tenant_and_user() -> None:
    cfg = SubstrateConfig(
        SESSION_INDEX_NAMESPACE_URI="http://seaweedfs:9101",
        SESSION_INDEX_BUCKET="my-bucket",
    )
    store = build_session_index_vector_store(cfg, "tenant-a", "user-a")

    assert store._namespace_uri == "http://seaweedfs:9101"
    assert store._namespace_path == ["my-bucket", "tenant-a", "user-a"]


@pytest.mark.parametrize("bad_id", ["", "../other", "a/b"])
def test_rejects_traversal_in_tenant_or_user_id(bad_id: str) -> None:
    cfg = SubstrateConfig()
    with pytest.raises(ValueError):
        build_session_index_vector_store(cfg, bad_id, "user-a")
    with pytest.raises(ValueError):
        build_session_index_vector_store(cfg, "tenant-a", bad_id)


def test_page_index_memory_local_mode_scopes_path(tmp_path) -> None:
    cfg = SubstrateConfig(SESSION_INDEX_LOCAL_PATH=str(tmp_path))
    memory = build_page_index_memory(cfg, "tenant-a", "user-a")

    assert isinstance(memory, LanceMemoryStore)
    assert memory._namespace_uri is None
    assert memory._path == str(tmp_path / "tenants/tenant-a/users/user-a/index")
    assert memory._table_name == "pageindex_trees"


def test_page_index_memory_namespace_mode_scopes_path() -> None:
    cfg = SubstrateConfig(
        SESSION_INDEX_NAMESPACE_URI="http://seaweedfs:9101",
        SESSION_INDEX_BUCKET="my-bucket",
    )
    memory = build_page_index_memory(cfg, "tenant-a", "user-a")

    assert memory._namespace_uri == "http://seaweedfs:9101"
    assert memory._namespace_path == ["my-bucket", "tenant-a", "user-a"]


def test_session_graph_store_local_mode_scopes_path(tmp_path) -> None:
    cfg = SubstrateConfig(SESSION_INDEX_LOCAL_PATH=str(tmp_path))
    store = build_session_graph_store(cfg, "tenant-a", "user-a")

    assert isinstance(store, LanceGraphStore)
    assert store._namespace_uri is None
    assert store._path == str(tmp_path / "tenants/tenant-a/users/user-a/index")


def test_session_graph_store_namespace_mode_scopes_path() -> None:
    cfg = SubstrateConfig(
        SESSION_INDEX_NAMESPACE_URI="http://seaweedfs:9101",
        SESSION_INDEX_BUCKET="my-bucket",
    )
    store = build_session_graph_store(cfg, "tenant-a", "user-a")

    assert store._namespace_uri == "http://seaweedfs:9101"
    assert store._namespace_path == ["my-bucket", "tenant-a", "user-a"]
