"""The per-(tenant, user) session-index factories.

A per-user store, not a single shared instance the way the main store's vectors are: the folder depends on tenant_id/user_id, which
isn't known until a real request exists. These tests pin where each store lives and that tenant_id/user_id are
validated the same way every other object-storage key is (layout.py's `_id()`), not accepted as raw path segments.
"""

from __future__ import annotations

import pytest

from substrate.stores.graph_tables import Graph
from substrate.stores.memory_tables import Memory
from substrate.stores.vector_tables import Vectors
from substrate_cloud.config import SubstrateConfig
from substrate_cloud.factory import (
    build_page_index_memory,
    build_session_graph_store,
    build_session_index_vector_store,
)


def test_the_vector_store_is_a_store_of_its_own_in_the_users_index_folder(tmp_path) -> None:
    cfg = SubstrateConfig(SESSION_INDEX_LOCAL_PATH=str(tmp_path))
    store = build_session_index_vector_store(cfg, "tenant-a", "user-a")

    assert isinstance(store, Vectors)
    assert store.store.root == tmp_path / "tenants/tenant-a/users/user-a/index"


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

    assert isinstance(memory, Memory)
    assert memory.store.root == tmp_path / "tenants/tenant-a/users/user-a/index"


def test_page_index_memory_is_a_store_of_its_own_in_the_users_index_folder(tmp_path) -> None:
    """Erasing a user's index is still removing one folder, because the trees live inside it — in any deployment."""
    cfg = SubstrateConfig(SESSION_INDEX_LOCAL_PATH=str(tmp_path))
    memory = build_page_index_memory(cfg, "tenant-a", "user-a")

    assert memory.store.root == tmp_path / "tenants/tenant-a/users/user-a/index"


def test_session_graph_store_is_a_store_of_its_own_in_the_users_index_folder(tmp_path) -> None:
    cfg = SubstrateConfig(SESSION_INDEX_LOCAL_PATH=str(tmp_path))
    store = build_session_graph_store(cfg, "tenant-a", "user-a")

    assert isinstance(store, Graph)
    assert store.store.root == tmp_path / "tenants/tenant-a/users/user-a/index"
