"""S3-compatible file store backed by MinIOConnector (L2)."""

from __future__ import annotations

from ravi.infrastructure.storage.minio import MinIOConnector


class S3FileStore:
    """Async file store that delegates to MinIOConnector (aiobotocore-based).

    Compatible with MinIO (docker-compose) and AWS S3.
    """

    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str = "us-east-1",
    ) -> None:
        self._connector = MinIOConnector(
            endpoint_url=endpoint_url,
            access_key=access_key,
            secret_key=secret_key,
            default_bucket=bucket,
            region=region,
        )
        self._bucket = bucket

    async def connect(self) -> None:
        await self._connector.connect()
        await self._ensure_bucket()

    async def disconnect(self) -> None:
        await self._connector.disconnect()

    async def _ensure_bucket(self) -> None:
        """Create the bucket on first start if it doesn't exist."""
        import botocore.exceptions

        session = self._connector._session
        assert session is not None, "connector not connected"
        async with session.create_client(
            "s3",
            endpoint_url=self._connector._endpoint_url,
            aws_access_key_id=self._connector._access_key,
            aws_secret_access_key=self._connector._secret_key,
            region_name=self._connector._region,
        ) as client:
            try:
                await client.head_bucket(Bucket=self._bucket)
            except botocore.exceptions.ClientError:
                await client.create_bucket(Bucket=self._bucket)

    async def upload(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> None:
        await self._connector.upload(key, data, content_type=content_type, bucket=self._bucket)

    async def download(self, key: str) -> bytes:
        return await self._connector.download(key, bucket=self._bucket)

    async def delete(self, key: str) -> None:
        await self._connector.delete(key, bucket=self._bucket)

    async def presign_url(self, key: str, *, expires_in: int = 3600) -> str:
        return await self._connector.presign_url(key, bucket=self._bucket, expires_in=expires_in)
