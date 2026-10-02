"""The S3 file store (against an in-memory bucket in place of aiobotocore), held to the conformance suite."""

from __future__ import annotations

from contextlib import asynccontextmanager

import botocore.exceptions
import pytest

from substrate.integrations.storage.s3 import S3FileStore
from substrate.testing.conformance.file_store import FileStoreConformance
from tests.integrations.test_s3_file_store import FakeConnector


class _Client:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self._objects = objects

    async def head_object(self, *, Bucket: str, Key: str) -> dict:
        if Key not in self._objects:
            raise botocore.exceptions.ClientError(
                {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
            )
        return {}


class Bucket(FakeConnector):
    """``FakeConnector`` plus the rest of what ``S3FileStore`` asks of a connector."""

    async def delete_prefix(self, prefix, *, bucket=None) -> int:
        doomed = [k for k in self.objects if k.startswith(prefix)]
        for k in doomed:
            del self.objects[k]
        return len(doomed)

    async def presign_url(self, key, *, bucket=None, expires_in=3600) -> str:
        return f"https://example.invalid/{key}?expires={expires_in}"

    @asynccontextmanager
    async def _client_ctx(self):
        yield _Client(self.objects)


class TestS3FileStore(FileStoreConformance):
    @pytest.fixture
    async def store(self):
        fs = S3FileStore(
            endpoint_url="http://localhost:9000",
            access_key="k",
            secret_key="s",
            bucket="test",
            user_quota_bytes=10**9,
        )
        fs._connector = Bucket()  # type: ignore[assignment]
        return fs
