"""The store's files — key rules, quota enforcement, usage accounting, and the on-disk contents."""

from __future__ import annotations

import pytest

from substrate.stores import Store, WorkspacePathError, WorkspaceQuotaExceededError


@pytest.fixture
async def store(tmp_path):
    opened = Store.at(tmp_path, file_quota_bytes=1000)
    yield opened.files
    await opened.aclose()


def blobs(root):
    """Every file of contents the store holds on disk."""
    return sorted(p for p in (root / "files").rglob("*") if p.is_file())


async def test_upload_download_round_trip(store):
    key = "tenants/t1/conversations/c1/workspace/shared/hello.txt"
    await store.upload(key, b"hello world", content_type="text/plain")
    assert await store.download(key) == b"hello world"


async def test_download_missing_raises_keyerror(store):
    with pytest.raises(KeyError):
        await store.download("tenants/t1/conversations/c1/workspace/shared/missing.txt")


async def test_delete_removes_the_file_and_its_contents(store, tmp_path):
    key = "tenants/t1/conversations/c1/workspace/shared/hello.txt"
    await store.upload(key, b"data")
    assert len(blobs(tmp_path)) == 1

    await store.delete(key)

    with pytest.raises(KeyError):
        await store.download(key)
    assert blobs(tmp_path) == [] and (tmp_path / "files").exists()


@pytest.mark.parametrize(
    "bad_key",
    [
        "../escape.txt",
        "/etc/passwd",
        "tenants/t1/../../escape.txt",
        "",
        "tenants/t1/a\x00b",
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


async def test_list_all_tenants_is_empty_before_anything_is_stored(store):
    assert await store.list_all_tenants() == []


async def test_list_conversations(store):
    # Conversations nest under their owning user, not directly under the
    # tenant (agents/workspace/layout.py) — c1/c2 belong to different
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


async def test_replacing_a_file_leaves_exactly_one_contents_on_disk(store, tmp_path):
    key = "tenants/t1/users/u1/uploads/a.bin"
    await store.upload(key, b"one")
    await store.upload(key, b"three")

    assert await store.download(key) == b"three" and len(blobs(tmp_path)) == 1


async def test_a_refused_upload_leaves_nothing_behind(store, tmp_path):
    with pytest.raises(WorkspaceQuotaExceededError):
        await store.upload("tenants/t1/users/u1/uploads/big.bin", b"x" * 1001)

    assert blobs(tmp_path) == [] and await store.list_prefix("tenants/t1/") == []


async def test_two_uploads_racing_for_the_last_bytes_cannot_both_fit(tmp_path):
    """The quota is checked in the transaction that writes. Two stores on one folder — two processes — upload at once into
    a tenant with room for only one of them."""
    import asyncio

    first, second = Store.at(tmp_path, file_quota_bytes=1000), Store.at(tmp_path, file_quota_bytes=1000)

    results = await asyncio.gather(
        first.files.upload("tenants/t1/a.bin", b"a" * 700),
        second.files.upload("tenants/t1/b.bin", b"b" * 700),
        return_exceptions=True,
    )

    assert sum(isinstance(r, WorkspaceQuotaExceededError) for r in results) == 1
    assert await first.files.usage_bytes("t1") == 700 and len(blobs(tmp_path)) == 1
    await first.aclose()
    await second.aclose()


async def test_copying_a_prefix_shares_contents_and_deleting_one_copy_keeps_the_other(store, tmp_path):
    await store.upload("tenants/t1/users/u1/conversations/c1/workspace/a.txt", b"shared bytes")
    await store.upload("tenants/t1/users/u1/conversations/c1/workspace/sub/b.txt", b"more")

    assert await store.copy_prefix("tenants/t1/users/u1/conversations/c1/workspace", "tenants/t1/users/u1/conversations/c2/workspace") == 2
    await store.delete_prefix("tenants/t1/users/u1/conversations/c1/workspace")

    assert await store.download("tenants/t1/users/u1/conversations/c2/workspace/sub/b.txt") == b"more"
    assert await store.list_prefix("tenants/t1/users/u1/conversations/c1/") == []
    assert len(blobs(tmp_path)) == 2


async def test_a_copy_that_would_exceed_the_quota_copies_nothing(store, tmp_path):
    await store.upload("tenants/t1/users/u1/w/a.bin", b"x" * 400)
    await store.upload("tenants/t1/users/u1/w/b.bin", b"y" * 400)

    with pytest.raises(WorkspaceQuotaExceededError):
        await store.copy_prefix("tenants/t1/users/u1/w", "tenants/t1/users/u1/copy")

    assert await store.list_prefix("tenants/t1/users/u1/copy") == [] and len(blobs(tmp_path)) == 2


async def test_contents_a_crash_left_unreferenced_are_collected_but_a_fresh_one_is_not(store, tmp_path):
    """A crash between writing contents and committing their row leaves a blob no row names. Collection removes those,
    but only once they are old: a young one may belong to a write another process is still finishing."""
    import os
    import time

    await store.upload("tenants/t1/users/u1/a.bin", b"kept")
    orphan = tmp_path / "files" / "ab" / "abandoned"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"orphan")

    assert await store.collect_garbage() == 0 and orphan.exists(), "a young orphan was collected"
    long_ago = time.time() - 7200
    os.utime(orphan, (long_ago, long_ago))
    assert await store.collect_garbage() == 1 and not orphan.exists()
    assert await store.download("tenants/t1/users/u1/a.bin") == b"kept"


async def test_a_kill_between_writing_contents_and_committing_the_row_loses_nothing_that_was_committed(tmp_path):
    """The contents are on disk before any row names them. A process killed in that window leaves the earlier version of
    the file intact and readable — there is no state in which the row names contents that are not there."""
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent(
        """
        import asyncio, os, sys
        from substrate.stores import Store

        async def main():
            store = Store.at(sys.argv[1])
            await store.files.upload("tenants/t1/a.bin", b"committed")
            from substrate.stores import file_tables
            file_tables._flush_to_disk(store.files._path("deadbeef"), b"half done")  # contents written, row never committed
            os._exit(5)

        asyncio.run(main())
        """
    )
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 5, result.stderr

    reopened = Store.at(tmp_path)
    assert await reopened.files.download("tenants/t1/a.bin") == b"committed"
    assert [k for k, _s, _m in await reopened.files.list_prefix("tenants/t1")] == ["tenants/t1/a.bin"]
    await reopened.aclose()
