"""Tests for the pluggable file-storage layer.

Covers:
  - LocalFileStore: put, get, get_stream, exists, head, delete, delete_prefix,
    list_keys, copy, path-traversal protection
  - TenantContext: prefix, key, scope_prefix, sanitisation
  - EncryptedFileStore: round-trip put/get, head metadata, copy re-encryption
  - Factory: create_file_store for local and encryption wrappers
  - FileRef: immutable value object
"""

from __future__ import annotations

import hashlib
import os

import pytest

from raavan.core.storage.base import FileRef
from raavan.core.storage.local import LocalFileStore
from raavan.core.storage.tenant import FileScope, TenantContext


# -- FileRef -----------------------------------------------------------------


def test_file_ref_immutable():
    ref = FileRef(
        object_key="test/file.txt",
        size_bytes=100,
        content_type="text/plain",
        checksum_sha256="abc123",
    )
    assert ref.object_key == "test/file.txt"
    assert ref.size_bytes == 100
    with pytest.raises(AttributeError):
        ref.object_key = "changed"  # type: ignore[misc]


def test_file_ref_default_metadata():
    ref = FileRef(
        object_key="k",
        size_bytes=0,
        content_type="application/octet-stream",
        checksum_sha256="",
    )
    assert ref.metadata == {}
    assert ref.created_at is not None


# -- TenantContext -----------------------------------------------------------


class TestTenantContext:
    def test_full_prefix(self):
        ctx = TenantContext(org_id="acme", user_id="u-1", thread_id="t-2")
        assert ctx.prefix() == "org/acme/user/u-1/thread/t-2/"

    def test_no_org_prefix(self):
        ctx = TenantContext(user_id="u-1", thread_id="t-2")
        assert ctx.prefix() == "user/u-1/thread/t-2/"

    def test_thread_only_prefix(self):
        ctx = TenantContext(thread_id="t-2")
        assert ctx.prefix() == "thread/t-2/"

    def test_global_prefix(self):
        ctx = TenantContext()
        assert ctx.prefix() == "global/"

    def test_key_with_unique(self):
        ctx = TenantContext(thread_id="t-1")
        key = ctx.key("report.csv", FileScope.UPLOADS)
        assert key.startswith("thread/t-1/uploads/")
        assert key.endswith("/report.csv")
        # UUID segment should be 16 hex chars
        parts = key.split("/")
        assert len(parts[3]) == 16

    def test_key_without_unique(self):
        ctx = TenantContext(thread_id="t-1")
        key = ctx.key("report.csv", FileScope.UPLOADS, unique=False)
        assert key == "thread/t-1/uploads/report.csv"

    def test_scope_prefix(self):
        ctx = TenantContext(org_id="x", thread_id="t-1")
        assert ctx.scope_prefix(FileScope.GENERATED) == "org/x/thread/t-1/generated/"

    def test_sanitise_filename_spaces(self):
        ctx = TenantContext(thread_id="t-1")
        key = ctx.key("my file (1).txt", FileScope.UPLOADS, unique=False)
        assert "my_file_1.txt" in key

    def test_sanitise_path_traversal(self):
        ctx = TenantContext(thread_id="t-1")
        key = ctx.key("../../etc/passwd", FileScope.UPLOADS, unique=False)
        # _sanitise_filename extracts basename ("passwd"), so no ".." in result
        assert ".." not in key
        assert key == "thread/t-1/uploads/passwd"

    def test_sanitise_empty_becomes_unnamed(self):
        ctx = TenantContext(thread_id="t-1")
        key = ctx.key("!!!!", FileScope.UPLOADS, unique=False)
        assert key.endswith("/unnamed")

    def test_immutable(self):
        ctx = TenantContext(org_id="a", thread_id="t")
        with pytest.raises(AttributeError):
            ctx.org_id = "b"  # type: ignore[misc]


# -- LocalFileStore ----------------------------------------------------------


class TestLocalFileStore:
    async def _make_store(self, tmp_path):
        root = tmp_path / "filestore"
        store = LocalFileStore(root=str(root))
        await store.startup()
        return store

    async def test_put_and_get(self, tmp_path):
        store = await self._make_store(tmp_path)
        data = b"hello world"
        ref = await store.put("test/file.txt", data, content_type="text/plain")

        assert ref.object_key == "test/file.txt"
        assert ref.size_bytes == len(data)
        assert ref.content_type == "text/plain"
        assert ref.checksum_sha256 == hashlib.sha256(data).hexdigest()

        retrieved = await store.get("test/file.txt")
        assert retrieved == data

    async def test_get_stream(self, tmp_path):
        store = await self._make_store(tmp_path)
        data = b"A" * 10000

        await store.put("big.bin", data)

        chunks = []
        stream = await store.get_stream("big.bin", chunk_size=1024)
        async for chunk in stream:
            chunks.append(chunk)
        assert b"".join(chunks) == data
        assert len(chunks) >= 2  # multiple chunks expected

    async def test_get_nonexistent_raises(self, tmp_path):
        store = await self._make_store(tmp_path)
        with pytest.raises(FileNotFoundError):
            await store.get("does/not/exist.txt")

    async def test_exists(self, tmp_path):
        store = await self._make_store(tmp_path)
        assert not await store.exists("test/nope.txt")
        await store.put("test/yep.txt", b"x")
        assert await store.exists("test/yep.txt")

    async def test_head(self, tmp_path):
        store = await self._make_store(tmp_path)
        data = b"metadata test"
        await store.put("meta.txt", data, content_type="text/plain")

        ref = await store.head("meta.txt")
        assert ref.size_bytes == len(data)
        assert ref.checksum_sha256 == hashlib.sha256(data).hexdigest()

    async def test_delete(self, tmp_path):
        store = await self._make_store(tmp_path)
        await store.put("del.txt", b"temp")
        assert await store.exists("del.txt")

        await store.delete("del.txt")
        assert not await store.exists("del.txt")

    async def test_delete_nonexistent_noop(self, tmp_path):
        store = await self._make_store(tmp_path)
        # Should not raise
        await store.delete("ghost.txt")

    async def test_delete_prefix(self, tmp_path):
        store = await self._make_store(tmp_path)
        await store.put("prefix/a.txt", b"a")
        await store.put("prefix/b.txt", b"b")
        await store.put("other/c.txt", b"c")

        count = await store.delete_prefix("prefix")
        assert count == 2
        assert not await store.exists("prefix/a.txt")
        assert not await store.exists("prefix/b.txt")
        assert await store.exists("other/c.txt")

    async def test_list_keys(self, tmp_path):
        store = await self._make_store(tmp_path)
        await store.put("ns/x.txt", b"x")
        await store.put("ns/y.txt", b"y")
        await store.put("ns/sub/z.txt", b"z")
        await store.put("other/w.txt", b"w")

        keys, cursor = await store.list_keys("ns")
        assert set(keys) == {"ns/x.txt", "ns/y.txt", "ns/sub/z.txt"}
        assert cursor is None  # all fit in one page

    async def test_list_keys_pagination(self, tmp_path):
        store = await self._make_store(tmp_path)
        for i in range(5):
            await store.put(f"pg/{i:03d}.txt", b"x")

        keys, cursor = await store.list_keys("pg", limit=3)
        assert len(keys) == 3
        assert cursor is not None

        keys2, cursor2 = await store.list_keys("pg", limit=3, cursor=cursor)
        assert len(keys2) == 2
        assert cursor2 is None

    async def test_copy(self, tmp_path):
        store = await self._make_store(tmp_path)
        await store.put("src.txt", b"original")

        ref = await store.copy("src.txt", "dst.txt")
        assert ref.object_key == "dst.txt"

        assert await store.get("dst.txt") == b"original"
        assert await store.get("src.txt") == b"original"

    async def test_path_traversal_blocked(self, tmp_path):
        store = await self._make_store(tmp_path)
        with pytest.raises(ValueError, match="Path traversal"):
            await store.put("../../etc/passwd", b"evil")

    async def test_get_url(self, tmp_path):
        store = await self._make_store(tmp_path)
        await store.put("url_test.txt", b"data")
        url = await store.get_url("url_test.txt")
        assert url.startswith("file://")

    async def test_put_async_iterator(self, tmp_path):
        store = await self._make_store(tmp_path)

        async def gen():
            yield b"chunk1"
            yield b"chunk2"

        ref = await store.put("stream.bin", gen())
        assert ref.size_bytes == 12  # len("chunk1chunk2")

        data = await store.get("stream.bin")
        assert data == b"chunk1chunk2"

    async def test_put_with_metadata(self, tmp_path):
        store = await self._make_store(tmp_path)
        meta = {"author": "test", "version": "1"}
        ref = await store.put("m.txt", b"data", metadata=meta)
        assert ref.metadata == meta


# -- EncryptedFileStore ------------------------------------------------------


class TestEncryptedFileStore:
    async def _make(self, tmp_path):
        from raavan.core.storage.encrypted import (
            EncryptedFileStore,
            LocalKeyProvider,
        )

        root = tmp_path / "encstore"
        inner = LocalFileStore(root=str(root))
        await inner.startup()

        key_hex = os.urandom(32).hex()
        kp = LocalKeyProvider(key_hex)
        enc = EncryptedFileStore(inner=inner, key_provider=kp)
        return enc, inner

    async def test_roundtrip(self, tmp_path):
        enc, _ = await self._make(tmp_path)
        data = b"secret document content"
        ref = await enc.put("secret.txt", data, content_type="text/plain")

        assert ref.size_bytes == len(data)
        assert ref.checksum_sha256 == hashlib.sha256(data).hexdigest()

        decrypted = await enc.get("secret.txt")
        assert decrypted == data

    async def test_encrypted_at_rest(self, tmp_path):
        enc, inner = await self._make(tmp_path)
        data = b"this should be encrypted"
        await enc.put("enc.txt", data)

        # Read raw bytes from the inner store -- should NOT equal cleartext
        raw = await inner.get("enc.txt")
        assert raw != data
        assert len(raw) > len(data)  # envelope overhead

    async def test_head_returns_ref(self, tmp_path):
        enc, _ = await self._make(tmp_path)
        data = b"short"
        await enc.put("tiny.txt", data)

        ref = await enc.head("tiny.txt")
        assert ref.size_bytes > 0
        assert ref.object_key == "tiny.txt"

    async def test_get_stream(self, tmp_path):
        enc, _ = await self._make(tmp_path)
        data = b"X" * 5000
        await enc.put("stream.bin", data)

        chunks = []
        stream = await enc.get_stream("stream.bin", chunk_size=1024)
        async for chunk in stream:
            chunks.append(chunk)
        assert b"".join(chunks) == data

    async def test_copy_re_encrypts(self, tmp_path):
        enc, _ = await self._make(tmp_path)
        data = b"important data"
        await enc.put("orig.txt", data)

        ref = await enc.copy("orig.txt", "copy.txt")
        assert ref.object_key == "copy.txt"

        # Both should decrypt to same content
        assert await enc.get("orig.txt") == data
        assert await enc.get("copy.txt") == data

    async def test_get_url_raises(self, tmp_path):
        enc, _ = await self._make(tmp_path)
        await enc.put("no_url.txt", b"x")

        with pytest.raises(NotImplementedError):
            await enc.get_url("no_url.txt")

    async def test_delete(self, tmp_path):
        enc, _ = await self._make(tmp_path)
        await enc.put("rm.txt", b"bye")
        assert await enc.exists("rm.txt")
        await enc.delete("rm.txt")
        assert not await enc.exists("rm.txt")

    async def test_list_keys(self, tmp_path):
        enc, _ = await self._make(tmp_path)
        await enc.put("ns/a.txt", b"a")
        await enc.put("ns/b.txt", b"b")

        keys, _ = await enc.list_keys("ns")
        assert set(keys) == {"ns/a.txt", "ns/b.txt"}

    async def test_delete_prefix(self, tmp_path):
        enc, _ = await self._make(tmp_path)
        await enc.put("pfx/a.txt", b"a")
        await enc.put("pfx/b.txt", b"b")

        count = await enc.delete_prefix("pfx")
        assert count == 2
        assert not await enc.exists("pfx/a.txt")


# -- Factory -----------------------------------------------------------------


class TestFactory:
    def test_create_local_store(self, tmp_path):
        from unittest.mock import MagicMock

        from raavan.core.storage.factory import create_file_store

        settings = MagicMock()
        settings.FILE_STORE_BACKEND = "local"
        settings.FILE_STORE_ROOT = str(tmp_path / "files")
        settings.FILE_ENCRYPTION_MODE = "none"
        settings.ROOT_DIR = tmp_path

        store = create_file_store(settings)
        assert isinstance(store, LocalFileStore)

    def test_create_encrypted_local_store(self, tmp_path):
        from unittest.mock import MagicMock

        from raavan.core.storage.encrypted import EncryptedFileStore
        from raavan.core.storage.factory import create_file_store

        settings = MagicMock()
        settings.FILE_STORE_BACKEND = "local"
        settings.FILE_STORE_ROOT = str(tmp_path / "files")
        settings.FILE_ENCRYPTION_MODE = "envelope"
        settings.FILE_KEK_HEX = os.urandom(32).hex()
        settings.ROOT_DIR = tmp_path

        store = create_file_store(settings)
        assert isinstance(store, EncryptedFileStore)

    def test_unknown_backend_raises(self):
        from unittest.mock import MagicMock

        from raavan.core.storage.factory import create_file_store

        settings = MagicMock()
        settings.FILE_STORE_BACKEND = "unknown"
        settings.FILE_ENCRYPTION_MODE = "none"

        with pytest.raises(ValueError, match="Unknown FILE_STORE_BACKEND"):
            create_file_store(settings)

    def test_envelope_without_kek_raises(self, tmp_path):
        from unittest.mock import MagicMock

        from raavan.core.storage.factory import create_file_store

        settings = MagicMock()
        settings.FILE_STORE_BACKEND = "local"
        settings.FILE_STORE_ROOT = str(tmp_path / "files")
        settings.FILE_ENCRYPTION_MODE = "envelope"
        settings.FILE_KEK_HEX = ""
        settings.ROOT_DIR = tmp_path

        with pytest.raises(ValueError, match="FILE_KEK_HEX"):
            create_file_store(settings)
