"""S3FileStore's workspace surface + MinIOConnector listing pagination.

Both are exercised against fakes rather than a live bucket: the logic under
test is the prefix accounting and the continuation-token loop, not aiobotocore.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest

from substrate.capabilities.storage.s3 import S3FileStore
from substrate.capabilities.storage.workspace import WorkspaceQuotaExceededError
from substrate.infrastructure.storage.minio import MinIOConnector


class FakeConnector:
    """In-memory stand-in for MinIOConnector, keyed like a real bucket."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def upload(self, key, data, *, content_type="", bucket=None) -> None:
        self.objects[key] = data

    async def download(self, key, *, bucket=None) -> bytes:
        return self.objects[key]

    async def delete(self, key, *, bucket=None) -> None:
        self.objects.pop(key, None)

    async def list_objects(self, *, prefix="", bucket=None, max_keys=None):
        return [
            {"key": k, "size": len(v), "mtime": 1000.0}
            for k, v in sorted(self.objects.items())
            if k.startswith(prefix)
        ]


@pytest.fixture
def store() -> S3FileStore:
    fs = S3FileStore(
        endpoint_url="http://localhost:9000",
        access_key="k",
        secret_key="s",
        bucket="test",
        user_quota_bytes=1000,
    )
    fs._connector = FakeConnector()  # pyright: ignore[reportAttributeAccessIssue]
    return fs


async def test_usage_bytes_sums_only_that_users_prefix(store):
    await store.upload("users/u1/uploads/a.bin", b"x" * 300)
    await store.upload("users/u1/sessions/t1/b.bin", b"x" * 200)
    await store.upload("users/u2/uploads/c.bin", b"x" * 500)

    assert await store.usage_bytes("u1", force=True) == 500
    assert await store.usage_bytes("u2", force=True) == 500


async def test_list_user_files_scopes_to_the_user(store):
    await store.upload("users/u1/sessions/t1/a.txt", b"aaa")
    await store.upload("users/u2/uploads/c.txt", b"c")

    files = await store.list_user_files("u1")

    assert [key for key, _size, _mtime in files] == ["users/u1/sessions/t1/a.txt"]
    assert files[0][1] == 3


async def test_quota_rejects_a_write_past_the_limit(store):
    await store.upload("users/u1/uploads/a.bin", b"x" * 900)
    with pytest.raises(WorkspaceQuotaExceededError):
        await store.upload("users/u1/uploads/b.bin", b"x" * 200)


async def test_overwrite_is_charged_its_delta_not_its_full_size(store):
    """Re-uploading a key replaces it, so it must not be counted as
    existing + new — the same rule WorkspaceFileStore.upload follows."""
    key = "users/u1/uploads/a.bin"
    await store.upload(key, b"x" * 900)
    await store.upload(key, b"y" * 900)  # would be 1800 if double-counted
    assert await store.usage_bytes("u1", force=True) == 900


async def test_quota_is_per_user(store):
    await store.upload("users/u1/uploads/a.bin", b"x" * 900)
    await store.upload("users/u2/uploads/a.bin", b"y" * 900)


async def test_keys_outside_users_are_not_charged_to_a_quota(store):
    """An unowned key has no user to bill, so quota can't apply — it must not
    be silently attributed to someone."""
    await store.upload("shared/reference.bin", b"x" * 5000)
    assert await store.usage_bytes("u1", force=True) == 0


async def test_usage_bytes_cache_is_invalidated_by_a_write(store):
    await store.upload("users/u1/uploads/a.bin", b"x" * 100)
    assert await store.usage_bytes("u1") == 100
    # Without invalidation this would still report 100 from the TTL cache.
    await store.upload("users/u1/uploads/b.bin", b"x" * 50)
    assert await store.usage_bytes("u1") == 150


async def test_delete_invalidates_usage_cache(store):
    await store.upload("users/u1/uploads/a.bin", b"x" * 100)
    assert await store.usage_bytes("u1") == 100
    await store.delete("users/u1/uploads/a.bin")
    assert await store.usage_bytes("u1") == 0


# ── MinIOConnector.list_objects pagination ────────────────────────────────────


class FakePagedClient:
    """Mimics S3's hard 1000-key response cap via continuation tokens."""

    PAGE = 2

    def __init__(self, keys: list[str]) -> None:
        self.keys = keys
        self.requests: list[dict] = []

    async def list_objects_v2(self, **kwargs):
        self.requests.append(kwargs)
        start = int(kwargs.get("ContinuationToken") or 0)
        page = self.keys[start : start + self.PAGE]
        end = start + len(page)
        return {
            "Contents": [
                {
                    "Key": k,
                    "Size": 1,
                    "LastModified": datetime(2026, 1, 1, tzinfo=timezone.utc),
                }
                for k in page
            ],
            "IsTruncated": end < len(self.keys),
            "NextContinuationToken": str(end),
        }


def _connector_with(client: FakePagedClient) -> MinIOConnector:
    connector = MinIOConnector(
        endpoint_url="http://localhost:9000",
        access_key="k",
        secret_key="s",
        default_bucket="test",
    )

    @asynccontextmanager
    async def ctx():
        yield client

    connector._client_ctx = ctx  # pyright: ignore[reportAttributeAccessIssue]
    return connector


async def test_list_objects_follows_continuation_tokens():
    """S3 caps a single response at 1000 keys regardless of MaxKeys, so a
    non-paginating listing truncates — which would make a prefix-sum quota
    under-report and quietly stop enforcing."""
    client = FakePagedClient([f"users/u1/f{i}.bin" for i in range(5)])
    connector = _connector_with(client)

    objects = await connector.list_objects(prefix="users/u1/")

    assert [o["key"] for o in objects] == [f"users/u1/f{i}.bin" for i in range(5)]
    assert len(client.requests) == 3  # 2 + 2 + 1


async def test_list_objects_max_keys_caps_the_total():
    client = FakePagedClient([f"users/u1/f{i}.bin" for i in range(5)])
    connector = _connector_with(client)

    objects = await connector.list_objects(prefix="users/u1/", max_keys=3)

    assert len(objects) == 3


async def test_list_objects_reports_mtime_as_a_float_epoch():
    """Both stores' listings must be interchangeable, so mtime matches
    os.stat().st_mtime's type rather than being a formatted string."""
    client = FakePagedClient(["users/u1/a.bin"])
    connector = _connector_with(client)

    objects = await connector.list_objects()

    assert objects[0]["mtime"] == datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
