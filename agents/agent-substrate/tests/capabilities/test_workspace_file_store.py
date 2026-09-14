"""WorkspaceFileStore — traversal guard, quota enforcement, usage accounting."""

from __future__ import annotations

import pytest

from substrate.capabilities.storage.workspace import (
    WorkspaceFileStore,
    WorkspacePathError,
    WorkspaceQuotaExceededError,
)


@pytest.fixture
async def store(tmp_path):
    fs = WorkspaceFileStore(root=tmp_path, user_quota_bytes=1000)
    await fs.connect()
    return fs


async def test_upload_download_round_trip(store):
    key = "tenants/t1/conversations/c1/workspace/shared/hello.txt"
    await store.upload(key, b"hello world", content_type="text/plain")
    assert await store.download(key) == b"hello world"


async def test_download_missing_raises_keyerror(store):
    with pytest.raises(KeyError):
        await store.download("tenants/t1/conversations/c1/workspace/shared/missing.txt")


async def test_delete_removes_file_and_prunes_empty_dirs(store, tmp_path):
    key = "tenants/t1/conversations/c1/workspace/shared/hello.txt"
    await store.upload(key, b"data")
    await store.delete(key)
    with pytest.raises(KeyError):
        await store.download(key)
    # conversations/c1 and conversations dirs should be pruned (empty),
    # tenants/t1 too, but the root itself must remain.
    assert not (tmp_path / "tenants" / "t1").exists()
    assert tmp_path.exists()


@pytest.mark.parametrize(
    "bad_key",
    [
        "../escape.txt",
        "/etc/passwd",
        "tenants/t1/../../escape.txt",
        "",
    ],
)
async def test_traversal_rejected(store, bad_key):
    with pytest.raises(WorkspacePathError):
        await store.upload(bad_key, b"x")


async def test_traversal_rejected_on_download_and_delete(store):
    with pytest.raises(WorkspacePathError):
        await store.download("../../etc/passwd")
    with pytest.raises(WorkspacePathError):
        await store.delete("../../etc/passwd")


async def test_quota_enforced(store):
    await store.upload("tenants/t1/users/u1/uploads/a.bin", b"x" * 600)
    with pytest.raises(WorkspaceQuotaExceededError):
        await store.upload("tenants/t1/users/u1/uploads/b.bin", b"y" * 500)


async def test_quota_is_per_tenant(store):
    await store.upload("tenants/t1/users/u1/uploads/a.bin", b"x" * 900)
    # A different tenant has their own 1000-byte budget.
    await store.upload("tenants/t2/users/u1/uploads/a.bin", b"y" * 900)


async def test_overwrite_does_not_double_count_against_quota(store):
    key = "tenants/t1/users/u1/uploads/a.bin"
    await store.upload(key, b"x" * 900)
    # Re-uploading the same key replaces it in place — must not be
    # rejected as if it were 900 (existing) + 900 (new) = 1800 bytes.
    await store.upload(key, b"y" * 900)
    assert await store.usage_bytes("t1", force=True) == 900


async def test_usage_bytes_counts_files_written_outside_upload(store, tmp_path):
    # Simulates a file the sandbox wrote directly to the mounted volume,
    # bypassing store.upload() — usage_bytes must still see it since it
    # walks the filesystem, not an internal ledger.
    conv_dir = tmp_path / "tenants" / "t1" / "conversations" / "c1" / "workspace" / "shared"
    conv_dir.mkdir(parents=True)
    (conv_dir / "generated.csv").write_bytes(b"z" * 42)
    assert await store.usage_bytes("t1", force=True) == 42


async def test_presign_url_returns_sentinel(store):
    url = await store.presign_url("tenants/t1/users/u1/uploads/a.bin")
    assert url == "workspace://tenants/t1/users/u1/uploads/a.bin"


async def test_list_prefix_scopes_to_the_prefix(store):
    await store.upload("tenants/t1/conversations/c1/workspace/shared/a.txt", b"aaa")
    await store.upload("tenants/t1/conversations/c2/workspace/shared/b.txt", b"bb")
    await store.upload("tenants/t2/users/u1/uploads/c.txt", b"c")

    files = await store.list_prefix("tenants/t1/")
    keys = {key for key, _, _ in files}
    assert keys == {
        "tenants/t1/conversations/c1/workspace/shared/a.txt",
        "tenants/t1/conversations/c2/workspace/shared/b.txt",
    }
    sizes = {key: size for key, size, _ in files}
    assert sizes["tenants/t1/conversations/c1/workspace/shared/a.txt"] == 3
    assert sizes["tenants/t1/conversations/c2/workspace/shared/b.txt"] == 2


async def test_list_prefix_empty_for_unknown_prefix(store):
    assert await store.list_prefix("tenants/nobody/") == []


async def test_list_all_tenants(store):
    await store.upload("tenants/t1/conversations/c1/workspace/shared/a.txt", b"a")
    await store.upload("tenants/t2/users/u1/uploads/b.txt", b"b")
    assert await store.list_all_tenants() == ["t1", "t2"]


async def test_list_all_tenants_empty_when_no_tenants_dir(store):
    assert await store.list_all_tenants() == []


async def test_list_conversations(store):
    # Conversations nest under their owning user, not directly under the
    # tenant (capabilities/storage/layout.py) — c1/c2 belong to different
    # users, so the drill-down must walk every user directory.
    await store.upload(
        "tenants/t1/users/u1/conversations/c1/workspace/shared/a.txt", b"aa"
    )
    await store.upload(
        "tenants/t1/users/u1/conversations/c1/workspace/shared/b.txt", b"b"
    )
    await store.upload(
        "tenants/t1/users/u2/conversations/c2/workspace/shared/c.txt", b"ccc"
    )
    # Not under conversations/ — must not show up as a "conversation".
    await store.upload("tenants/t1/users/u1/uploads/d.txt", b"dddd")

    conversations = {
        cid: (size, count) for cid, size, count in await store.list_conversations("t1")
    }
    assert conversations == {"c1": (3, 2), "c2": (3, 1)}


async def test_list_conversations_empty_for_unknown_tenant(store):
    assert await store.list_conversations("nobody") == []


async def test_effective_quota_defaults_to_global(store):
    assert store.effective_quota("t1") == 1000


async def test_set_quota_override_changes_effective_quota(store):
    store.set_quota_override("t1", 50)
    assert store.effective_quota("t1") == 50
    # Unrelated tenant unaffected.
    assert store.effective_quota("t2") == 1000


async def test_set_quota_override_none_resets_to_default(store):
    store.set_quota_override("t1", 50)
    store.set_quota_override("t1", None)
    assert store.effective_quota("t1") == 1000


async def test_upload_enforces_per_tenant_quota_override_not_global_default(store):
    # Global default is 1000 (fixture), well above 10 bytes — only the
    # override should be what upload() actually enforces.
    store.set_quota_override("t1", 10)
    with pytest.raises(WorkspaceQuotaExceededError) as exc_info:
        await store.upload("tenants/t1/users/u1/uploads/big.txt", b"x" * 11)
    assert exc_info.value.quota_bytes == 10

    # A different tenant, no override, still gets the global default.
    await store.upload("tenants/t2/users/u1/uploads/ok.txt", b"y" * 11)
