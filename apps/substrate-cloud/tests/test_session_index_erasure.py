"""Erasure for the per-user session-document index: removing the user's (or tenant's) folder."""

from __future__ import annotations

from pathlib import Path

from substrate.workspace.layout import user_index_prefix
from substrate_cloud.session_index.erasure import (
    erase_session_index,
    erase_session_index_for_tenant,
)


async def test_erase_session_index_removes_the_users_local_directory(tmp_path):
    tenant_id, user_id = "tenant-a", "user-1"
    index_dir = tmp_path / user_index_prefix(tenant_id, user_id)
    index_dir.mkdir(parents=True)
    (index_dir / "vectors.lance").mkdir()

    cfg = _cfg(tmp_path)
    removed = await erase_session_index(cfg, tenant_id, user_id)

    assert removed == 1
    assert not index_dir.exists()


async def test_erase_session_index_is_a_noop_when_nothing_was_ever_ingested(tmp_path):
    cfg = _cfg(tmp_path)
    removed = await erase_session_index(cfg, "tenant-a", "never-uploaded")
    assert removed == 0


async def test_erase_session_index_does_not_touch_a_sibling_users_directory(tmp_path):
    tenant_id = "tenant-a"
    keep_dir = tmp_path / user_index_prefix(tenant_id, "user-keep")
    keep_dir.mkdir(parents=True)
    (keep_dir / "vectors.lance").mkdir()
    erase_dir = tmp_path / user_index_prefix(tenant_id, "user-erase")
    erase_dir.mkdir(parents=True)

    cfg = _cfg(tmp_path)
    await erase_session_index(cfg, tenant_id, "user-erase")

    assert not erase_dir.exists()
    assert keep_dir.exists()


async def test_erase_session_index_for_tenant_erases_every_known_user(tmp_path):
    tenant_id = "tenant-a"
    for user_id in ("user-1", "user-2"):
        d = tmp_path / user_index_prefix(tenant_id, user_id)
        d.mkdir(parents=True)

    other_tenant_dir = tmp_path / user_index_prefix("tenant-b", "user-3")
    other_tenant_dir.mkdir(parents=True)

    cfg = _cfg(tmp_path)
    removed = await erase_session_index_for_tenant(
        cfg, tenant_id, {"user-1", "user-2"}
    )

    assert removed == 2
    assert not (tmp_path / user_index_prefix(tenant_id, "user-1")).exists()
    assert not (tmp_path / user_index_prefix(tenant_id, "user-2")).exists()
    assert other_tenant_dir.exists()


def _cfg(tmp_path: Path):
    class _Cfg:
        SESSION_INDEX_LOCAL_PATH = str(tmp_path)

    return _Cfg()


async def test_erasing_a_tenant_also_reaches_a_user_nobody_listed(tmp_path):
    """A user whose only trace is an uploaded document has no row to name them. The tenant's folder is removed whole."""
    stranger = tmp_path / user_index_prefix("tenant-a", "stranger")
    stranger.mkdir(parents=True)
    (stranger / "substrate.db").write_bytes(b"x")
    neighbour = tmp_path / user_index_prefix("tenant-b", "someone")
    neighbour.mkdir(parents=True)

    removed = await erase_session_index_for_tenant(_cfg(tmp_path), "tenant-a", set())

    assert removed == 1 and not stranger.exists() and not (tmp_path / "tenants" / "tenant-a").exists()
    assert neighbour.exists()
