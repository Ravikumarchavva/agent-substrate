"""S3-compatible FileStore driver.

Works with AWS S3, MinIO, DigitalOcean Spaces, Cloudflare R2, and any
other S3-compatible API.  Uses ``aiobotocore`` for fully async operations.

Requirements::

    uv add aiobotocore
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

from agent_framework.core.storage.base import FileRef, FileStore


class S3FileStore(FileStore):
    """S3-compatible object store driver.

    Parameters:
        bucket: S3 bucket name.
        endpoint_url: Override for MinIO / R2 / Spaces (e.g. ``"http://localhost:9000"``).
                      ``None`` uses the default AWS endpoint.
        region: AWS region (e.g. ``"us-east-1"``).
        access_key: AWS access key ID.
        secret_key: AWS secret access key.
        prefix: Optional key prefix prepended to all object keys.
    """

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str | None = None,
        region: str = "us-east-1",
        access_key: str | None = None,
        secret_key: str | None = None,
        prefix: str = "",
    ) -> None:
        self._bucket = bucket
        self._endpoint_url = endpoint_url
        self._region = region
        self._access_key = access_key
        self._secret_key = secret_key
        self._prefix = prefix.rstrip("/") + "/" if prefix else ""
        self._session: Any = None
        self._client: Any = None
        self._client_cm: Any = None

    def _full_key(self, key: str) -> str:
        return f"{self._prefix}{key}" if self._prefix else key

    def _strip_prefix(self, full_key: str) -> str:
        if self._prefix and full_key.startswith(self._prefix):
            return full_key[len(self._prefix):]
        return full_key

    # ── lifecycle ────────────────────────────────────────────────────────

    async def startup(self) -> None:
        try:
            from aiobotocore.session import AioSession
        except ModuleNotFoundError as exc:
            raise ModuleNotFoundError(
                "S3FileStore requires 'aiobotocore'.  Install with:  uv add aiobotocore"
            ) from exc

        kwargs: dict[str, Any] = {
            "region_name": self._region,
        }
        if self._endpoint_url:
            kwargs["endpoint_url"] = self._endpoint_url
        if self._access_key and self._secret_key:
            kwargs["aws_access_key_id"] = self._access_key
            kwargs["aws_secret_access_key"] = self._secret_key

        self._session = AioSession()
        self._client_cm = self._session.create_client("s3", **kwargs)
        self._client = await self._client_cm.__aenter__()

        # Ensure bucket exists (safe for MinIO; ignored if already present)
        try:
            await self._client.head_bucket(Bucket=self._bucket)
        except Exception:
            try:
                await self._client.create_bucket(Bucket=self._bucket)
            except Exception:
                pass  # bucket already exists or no permission

    async def shutdown(self) -> None:
        if self._client_cm is not None:
            await self._client_cm.__aexit__(None, None, None)
            self._client = None
            self._client_cm = None

    # ── write ────────────────────────────────────────────────────────────

    async def put(
        self,
        key: str,
        data: bytes | AsyncIterator[bytes],
        *,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> FileRef:
        if isinstance(data, bytes):
            blob = data
        else:
            chunks: list[bytes] = []
            async for chunk in data:
                chunks.append(chunk)
            blob = b"".join(chunks)

        full_key = self._full_key(key)
        put_kwargs: dict[str, Any] = {
            "Bucket": self._bucket,
            "Key": full_key,
            "Body": blob,
            "ContentType": content_type,
        }
        if metadata:
            put_kwargs["Metadata"] = metadata

        await self._client.put_object(**put_kwargs)

        return FileRef(
            object_key=key,
            size_bytes=len(blob),
            content_type=content_type,
            checksum_sha256=self._sha256(blob),
            metadata=metadata or {},
        )

    # ── read ─────────────────────────────────────────────────────────────

    async def get(self, key: str) -> bytes:
        full_key = self._full_key(key)
        try:
            resp = await self._client.get_object(Bucket=self._bucket, Key=full_key)
        except self._client.exceptions.NoSuchKey:
            raise FileNotFoundError(key)
        except Exception as exc:
            if "NoSuchKey" in str(exc) or "404" in str(exc):
                raise FileNotFoundError(key) from exc
            raise

        async with resp["Body"] as stream:
            return await stream.read()

    async def get_stream(self, key: str, chunk_size: int = 1024 * 256) -> AsyncIterator[bytes]:
        full_key = self._full_key(key)
        try:
            resp = await self._client.get_object(Bucket=self._bucket, Key=full_key)
        except Exception as exc:
            if "NoSuchKey" in str(exc) or "404" in str(exc):
                raise FileNotFoundError(key) from exc
            raise

        async def _iter() -> AsyncIterator[bytes]:
            async with resp["Body"] as stream:
                while True:
                    chunk = await stream.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk

        return _iter()

    async def get_url(self, key: str, *, expires_in: int = 3600) -> str:
        full_key = self._full_key(key)
        if not await self.exists(key):
            raise FileNotFoundError(key)

        url: str = await self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": full_key},
            ExpiresIn=expires_in,
        )
        return url

    # ── metadata / existence ─────────────────────────────────────────────

    async def exists(self, key: str) -> bool:
        full_key = self._full_key(key)
        try:
            await self._client.head_object(Bucket=self._bucket, Key=full_key)
            return True
        except Exception:
            return False

    async def head(self, key: str) -> FileRef:
        full_key = self._full_key(key)
        try:
            resp = await self._client.head_object(Bucket=self._bucket, Key=full_key)
        except Exception as exc:
            if "404" in str(exc) or "NoSuchKey" in str(exc):
                raise FileNotFoundError(key) from exc
            raise

        return FileRef(
            object_key=key,
            size_bytes=resp.get("ContentLength", 0),
            content_type=resp.get("ContentType", "application/octet-stream"),
            checksum_sha256=resp.get("Metadata", {}).get("sha256", ""),
            created_at=resp.get("LastModified", datetime.now(timezone.utc)),
            metadata=resp.get("Metadata", {}),
        )

    # ── delete ───────────────────────────────────────────────────────────

    async def delete(self, key: str) -> None:
        full_key = self._full_key(key)
        await self._client.delete_object(Bucket=self._bucket, Key=full_key)

    async def delete_prefix(self, prefix: str) -> int:
        full_prefix = self._full_key(prefix)
        count = 0
        paginator = self._client.get_paginator("list_objects_v2")

        async for page in paginator.paginate(Bucket=self._bucket, Prefix=full_prefix):
            contents = page.get("Contents", [])
            if not contents:
                continue

            objects = [{"Key": obj["Key"]} for obj in contents]
            await self._client.delete_objects(
                Bucket=self._bucket,
                Delete={"Objects": objects, "Quiet": True},
            )
            count += len(objects)

        return count

    # ── list ─────────────────────────────────────────────────────────────

    async def list_keys(
        self,
        prefix: str = "",
        *,
        limit: int = 1000,
        cursor: Optional[str] = None,
    ) -> tuple[list[str], Optional[str]]:
        full_prefix = self._full_key(prefix)
        kwargs: dict[str, Any] = {
            "Bucket": self._bucket,
            "Prefix": full_prefix,
            "MaxKeys": limit,
        }
        if cursor:
            kwargs["StartAfter"] = self._full_key(cursor)

        resp = await self._client.list_objects_v2(**kwargs)
        contents = resp.get("Contents", [])

        keys = [self._strip_prefix(obj["Key"]) for obj in contents]
        next_cursor = keys[-1] if resp.get("IsTruncated") else None
        return keys, next_cursor

    # ── copy (server-side) ───────────────────────────────────────────────

    async def copy(self, src_key: str, dst_key: str) -> FileRef:
        full_src = self._full_key(src_key)
        full_dst = self._full_key(dst_key)

        if not await self.exists(src_key):
            raise FileNotFoundError(src_key)

        await self._client.copy_object(
            Bucket=self._bucket,
            CopySource={"Bucket": self._bucket, "Key": full_src},
            Key=full_dst,
        )

        return await self.head(dst_key)
