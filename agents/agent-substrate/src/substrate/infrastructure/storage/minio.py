"""MinIOConnector — upload, download, list, and presign objects in MinIO/S3."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List

from substrate.logger import setup_logging

if TYPE_CHECKING:
    from aiobotocore.session import AioSession

logger = setup_logging()


class MinIOConnector:
    """Async MinIO/S3-compatible object storage connector.

    Parameters
    ----------
    endpoint_url
        MinIO endpoint (e.g. ``http://localhost:9000``).
    access_key / secret_key
        Credentials.
    default_bucket
        Bucket to use when not specified per-call.
    """

    def __init__(
        self,
        endpoint_url: str = "",
        access_key: str = "",
        secret_key: str = "",
        default_bucket: str = "agent-data",
        region: str = "us-east-1",
    ) -> None:
        self._endpoint_url = endpoint_url
        self._access_key = access_key
        self._secret_key = secret_key
        self._default_bucket = default_bucket
        self._region = region
        self._session: AioSession | None = None

    async def connect(self) -> None:
        """Create aiobotocore session."""
        import aiobotocore.session

        self._session = aiobotocore.session.get_session()

    async def disconnect(self) -> None:
        """Cleanup (session is lightweight, no explicit close needed)."""
        self._session = None

    def _client_ctx(self) -> Any:
        """Return an async context manager for an S3 client."""
        assert self._session is not None, "Not connected"
        return self._session.create_client(
            "s3",
            endpoint_url=self._endpoint_url,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            region_name=self._region,
        )

    async def upload(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
        bucket: str | None = None,
    ) -> Dict[str, Any]:
        """Upload an object."""
        b = bucket or self._default_bucket
        async with self._client_ctx() as client:
            await client.put_object(
                Bucket=b, Key=key, Body=data, ContentType=content_type
            )
        return {"bucket": b, "key": key, "size": len(data)}

    async def download(
        self,
        key: str,
        *,
        bucket: str | None = None,
    ) -> bytes:
        """Download an object."""
        b = bucket or self._default_bucket
        async with self._client_ctx() as client:
            resp = await client.get_object(Bucket=b, Key=key)
            async with resp["Body"] as stream:
                return await stream.read()

    async def list_objects(
        self,
        *,
        prefix: str = "",
        bucket: str | None = None,
        max_keys: int | None = None,
    ) -> List[Dict[str, Any]]:
        """List objects under *prefix* as ``{key, size, mtime}`` dicts.

        Follows continuation tokens to completion by default. S3 caps a single
        ``list_objects_v2`` response at 1000 keys regardless of ``MaxKeys``, so
        a non-paginating version silently truncates — which would make a
        prefix-sum (storage quota accounting) *under*-report and quietly stop
        enforcing the limit. ``max_keys`` caps the total returned when a caller
        genuinely only wants a page; ``None`` means everything.
        """
        b = bucket or self._default_bucket
        objects: List[Dict[str, Any]] = []
        token: str | None = None
        async with self._client_ctx() as client:
            while True:
                kwargs: Dict[str, Any] = {"Bucket": b, "Prefix": prefix}
                if token is not None:
                    kwargs["ContinuationToken"] = token
                if max_keys is not None:
                    kwargs["MaxKeys"] = max_keys - len(objects)
                resp = await client.list_objects_v2(**kwargs)
                for obj in resp.get("Contents", []):
                    objects.append(
                        {
                            "key": obj["Key"],
                            "size": obj["Size"],
                            # A float epoch, matching os.stat().st_mtime, so a
                            # caller can treat either store's listing alike.
                            "mtime": obj["LastModified"].timestamp(),
                        }
                    )
                if max_keys is not None and len(objects) >= max_keys:
                    return objects[:max_keys]
                if not resp.get("IsTruncated"):
                    return objects
                token = resp.get("NextContinuationToken")
                if not token:
                    return objects

    async def presign_url(
        self,
        key: str,
        *,
        bucket: str | None = None,
        expires_in: int = 3600,
    ) -> str:
        """Generate a presigned URL for an object."""
        b = bucket or self._default_bucket
        async with self._client_ctx() as client:
            url: str = await client.generate_presigned_url(
                "get_object",
                Params={"Bucket": b, "Key": key},
                ExpiresIn=expires_in,
            )
            return url

    async def delete(
        self,
        key: str,
        *,
        bucket: str | None = None,
    ) -> None:
        """Delete an object."""
        b = bucket or self._default_bucket
        async with self._client_ctx() as client:
            await client.delete_object(Bucket=b, Key=key)
