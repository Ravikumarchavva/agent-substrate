"""Conformance suite for ``FileStore``.

Every implementation — in-memory, local workspace directory, S3, any a consumer writes — runs exactly
these tests: keyed bytes round-trip, prefixes behave as directories, a tenant's usage counts only its
own keys, and a hostile key is either refused or stored inertly — never an escape. Subclass it and
provide the ``store`` fixture.
"""

from __future__ import annotations

import pytest

from substrate.stores.files import FileStore

T1 = "tenants/t1/users/u1/"
T2 = "tenants/t2/users/u1/"


class FileStoreConformance:
    @pytest.fixture
    async def store(self) -> FileStore:  # pragma: no cover - supplied by subclasses
        raise NotImplementedError

    async def test_bytes_round_trip_and_are_replaced_on_upload(self, store: FileStore) -> None:
        await store.upload(T1 + "a.bin", b"one")
        assert await store.download(T1 + "a.bin") == b"one"
        await store.upload(T1 + "a.bin", b"two!")
        assert await store.download(T1 + "a.bin") == b"two!"

    async def test_downloading_a_missing_key_raises(self, store: FileStore) -> None:
        with pytest.raises(Exception):
            await store.download(T1 + "nope")

    async def test_exists_is_a_point_check(self, store: FileStore) -> None:
        await store.upload(T1 + "a.bin", b"x")
        assert await store.exists(T1 + "a.bin") is True
        assert await store.exists(T1 + "b.bin") is False

    async def test_delete_removes_the_key_and_a_missing_key_is_a_no_op(self, store: FileStore) -> None:
        await store.upload(T1 + "a.bin", b"x")
        await store.delete(T1 + "a.bin")
        assert await store.exists(T1 + "a.bin") is False
        await store.delete(T1 + "a.bin")

    async def test_list_prefix_reports_key_size_and_mtime_for_that_prefix_only(self, store: FileStore) -> None:
        await store.upload(T1 + "docs/a.bin", b"12345")
        await store.upload(T1 + "docs/sub/b.bin", b"123")
        await store.upload(T1 + "other/c.bin", b"1")
        listed = {key: size for key, size, _mtime in await store.list_prefix(T1 + "docs/")}
        assert listed == {T1 + "docs/a.bin": 5, T1 + "docs/sub/b.bin": 3}
        assert all(isinstance(m, float) for _k, _s, m in await store.list_prefix(T1 + "docs/"))

    async def test_listing_an_empty_prefix_finds_nothing(self, store: FileStore) -> None:
        assert await store.list_prefix(T1 + "nothing/") == []

    async def test_delete_prefix_removes_only_that_prefix_and_counts_it(self, store: FileStore) -> None:
        await store.upload(T1 + "docs/a.bin", b"a")
        await store.upload(T1 + "docs/b.bin", b"b")
        await store.upload(T1 + "keep/c.bin", b"c")
        await store.upload(T2 + "docs/a.bin", b"other tenant")
        assert await store.delete_prefix(T1 + "docs/") == 2
        assert await store.exists(T1 + "docs/a.bin") is False
        assert await store.exists(T1 + "keep/c.bin") is True
        assert await store.exists(T2 + "docs/a.bin") is True

    async def test_copy_prefix_keeps_relative_paths_and_leaves_the_source(self, store: FileStore) -> None:
        await store.upload(T1 + "src/a.bin", b"a")
        await store.upload(T1 + "src/deep/b.bin", b"b")
        assert await store.copy_prefix(T1 + "src/", T1 + "dst/") == 2
        assert await store.download(T1 + "dst/a.bin") == b"a"
        assert await store.download(T1 + "dst/deep/b.bin") == b"b"
        assert await store.exists(T1 + "src/a.bin") is True

    async def test_usage_counts_only_that_tenants_keys(self, store: FileStore) -> None:
        await store.upload(T1 + "a.bin", b"x" * 300)
        await store.upload("tenants/t1/conversations/c/b.bin", b"x" * 200)
        await store.upload(T2 + "c.bin", b"x" * 500)
        assert await store.usage_bytes("t1", force=True) == 500
        assert await store.usage_bytes("t2", force=True) == 500
        assert await store.usage_bytes("nobody", force=True) == 0

    async def test_presign_returns_a_string_for_the_key(self, store: FileStore) -> None:
        await store.upload(T1 + "a.bin", b"x")
        assert isinstance(await store.presign_url(T1 + "a.bin"), str)

    @pytest.mark.parametrize("hostile", ["tenants/t1/../../../etc/passwd", "../../outside", "tenants/t1/users/u1/../../../t2/users/u1/secret"])
    async def test_a_hostile_key_is_refused_or_inert_never_an_escape(self, store: FileStore, hostile: str) -> None:
        await store.upload(T2 + "secret", b"keep me")
        try:
            await store.upload(hostile, b"attack")
        except ValueError:
            pass  # refused outright
        assert await store.download(T2 + "secret") == b"keep me", "a hostile key overwrote another tenant's object"


__all__ = ["FileStoreConformance"]
