"""S3Connector — upload, download, list, and presign objects against any
S3-compatible object store.

No dependency on MinIO the software: this speaks the plain S3 API via
aiobotocore, so it works unchanged against SeaweedFS (this stack's default
self-hosted backend — see deployment/docker/docker-compose.yml), real AWS
S3, or any other S3-compatible service (Garage, R2, ...)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List

from substrate.logger import setup_logging

if TYPE_CHECKING:
    from aiobotocore.session import AioSession

logger = setup_logging()


class _NoopClientContext:
    """Adapts an already-open aiobotocore client to the
    ``async with ... as client:`` shape every method below already uses,
    without re-entering/closing the real client on every call."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def __aenter__(self) -> Any:
        return self._client

    async def __aexit__(self, *exc_info: Any) -> None:
        return None


class S3Connector:
    """Async S3-compatible object storage connector — SeaweedFS (default),
    real AWS S3, or any other S3-compatible service, all through the same
    aiobotocore client.

    Parameters
    ----------
    endpoint_url
        S3-compatible endpoint (e.g. ``http://localhost:9000``). Leave empty
        (the default) to talk to real AWS S3 — boto3 resolves the regional
        endpoint itself from ``region``, but only when it receives ``None``,
        not an empty string, hence the ``or None`` below.
    access_key / secret_key
        Credentials. Leave both empty (the default) to fall back to boto3's
        standard credential chain (env vars, ``~/.aws/credentials``, an IAM
        role) — again requires ``None``, not ``""``, to actually trigger that
        fallback.
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
        # One client for the connector's lifetime, not one per operation —
        # real cost at scale: a multi-image-per-page ingestion run does tens
        # of thousands of uploads, each previously paying a fresh client
        # construction (TLS/connection setup) for what should be one
        # long-lived connection pool.
        self._client_cm: Any = None
        self._client: Any = None

    async def connect(self) -> None:
        """Create the aiobotocore session and one long-lived S3 client."""
        import aiobotocore.session

        self._session = aiobotocore.session.get_session()
        self._client_cm = self._session.create_client(
            "s3",
            # Empty string must become None here, not be passed through —
            # botocore treats "" as a literal (invalid) endpoint override,
            # while None means "use the real AWS default resolution" /
            # "use the standard credential chain". See the class docstring.
            endpoint_url=self._endpoint_url or None,
            aws_access_key_id=self._access_key or None,
            aws_secret_access_key=self._secret_key or None,
            region_name=self._region,
        )
        self._client = await self._client_cm.__aenter__()

    async def disconnect(self) -> None:
        """Close the long-lived S3 client."""
        if self._client_cm is not None:
            await self._client_cm.__aexit__(None, None, None)
        self._client_cm = None
        self._client = None
        self._session = None

    def _client_ctx(self) -> Any:
        """Return the connector's long-lived S3 client, wrapped so existing
        ``async with self._client_ctx() as client:`` call sites need no
        change — entering/exiting this context is a no-op, the client
        outlives any single call."""
        assert self._client is not None, "Not connected"
        return _NoopClientContext(self._client)

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
