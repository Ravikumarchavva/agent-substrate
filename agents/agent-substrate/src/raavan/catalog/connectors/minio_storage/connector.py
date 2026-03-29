"""MinIOConnector — upload, download, list, and presign objects in MinIO/S3.

Re-uses the aiobotocore patterns from ``core/storage/s3.py`` but as a
standalone connector adapter.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


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
        self._session: Optional[Any] = None

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
        bucket: Optional[str] = None,
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
        bucket: Optional[str] = None,
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
        bucket: Optional[str] = None,
        max_keys: int = 100,
    ) -> List[Dict[str, Any]]:
        """List objects in a bucket."""
        b = bucket or self._default_bucket
        async with self._client_ctx() as client:
            resp = await client.list_objects_v2(
                Bucket=b, Prefix=prefix, MaxKeys=max_keys
            )
            return [
                {
                    "key": obj["Key"],
                    "size": obj["Size"],
                    "last_modified": str(obj["LastModified"]),
                }
                for obj in resp.get("Contents", [])
            ]

    async def presign_url(
        self,
        key: str,
        *,
        bucket: Optional[str] = None,
        expires_in: int = 3600,
    ) -> str:
        """Generate a presigned URL for an object."""
        b = bucket or self._default_bucket
        async with self._client_ctx() as client:
            url = await client.generate_presigned_url(
                "get_object",
                Params={"Bucket": b, "Key": key},
                ExpiresIn=expires_in,
            )
            return url

    async def delete(
        self,
        key: str,
        *,
        bucket: Optional[str] = None,
    ) -> None:
        """Delete an object."""
        b = bucket or self._default_bucket
        async with self._client_ctx() as client:
            await client.delete_object(Bucket=b, Key=key)
