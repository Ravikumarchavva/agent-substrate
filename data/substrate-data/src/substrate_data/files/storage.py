"""The volumes bucket: presigned transfers, multipart planning and server-side copies.

Only the control plane holds credentials for this bucket (workers and the agent host hold none). Object
keys are opaque (`volumes/{volume_id}/objects/{uuid}`); file names live in the catalog, never in storage.
"""

from __future__ import annotations

import base64
import binascii
import math
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from substrate_data.core.errors import Problem
from substrate_data.core.models import Settings

SINGLE_PUT_MAX = 64 * 1024 * 1024
MAX_PARTS = 10_000
MIN_PART = 5 * 1024 * 1024


def storage_unavailable() -> Problem:
    return Problem(503, "storage-unavailable", "File storage is unavailable")


def plan_parts(size: int, part_bytes: int) -> list[tuple[int, int, int]]:
    """`(part_number, offset, length)` for a multipart upload of `size` bytes; the last part is shorter."""
    if size <= 0:
        raise ValueError("A multipart upload needs at least one byte")
    if part_bytes < MIN_PART:
        raise ValueError("Parts must be at least 5 MiB")
    count = math.ceil(size / part_bytes)
    if count > MAX_PARTS:
        raise Problem(
            413,
            "payload-too-large",
            f"A file of {size} bytes needs more than {MAX_PARTS} parts of {part_bytes} bytes",
        )
    return [(n + 1, n * part_bytes, min(part_bytes, size - n * part_bytes)) for n in range(count)]


def sha256_b64(hex_digest: str) -> str:
    return base64.b64encode(binascii.unhexlify(hex_digest)).decode()


def sha256_hex(b64: str) -> str:
    return binascii.hexlify(base64.b64decode(b64)).decode()


@dataclass(frozen=True)
class ObjectInfo:
    size: int
    etag: str
    sha256: str | None  # hex, when the store computed and kept one


class VolumeStorage:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.bucket = settings.volumes_bucket
        credentials = dict(
            aws_access_key_id=settings.s3_access_key_id.get_secret_value(),
            aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        self.s3 = boto3.client("s3", endpoint_url=settings.s3_endpoint_url, **credentials)
        # Presigned URLs name the host the *client* reaches, which may differ from ours.
        public = settings.volumes_public_endpoint_url
        self.signer = boto3.client("s3", endpoint_url=public, **credentials) if public else self.s3
        self.ttl = settings.files_url_ttl_seconds

    # ------------------------------------------------------------------ keys
    @staticmethod
    def object_key(volume_id: str) -> str:
        return f"volumes/{volume_id}/objects/{uuid4().hex}"

    @staticmethod
    def session_key(session_id: str) -> str:
        return f"sessions/{session_id}/{uuid4().hex}"

    # ------------------------------------------------------------------ uploads
    def begin_upload(
        self, key: str, size: int, content_type: str, sha256: str = ""
    ) -> dict[str, Any]:
        """Instructions for sending `size` bytes to `key`: one presigned PUT, or multipart parts."""
        part = self.settings.files_part_bytes
        try:
            if size <= SINGLE_PUT_MAX:
                params: dict[str, Any] = {
                    "Bucket": self.bucket,
                    "Key": key,
                    "ContentType": content_type,
                    "ContentLength": size,
                }
                headers = {"Content-Type": content_type}
                if sha256:
                    params["ChecksumSHA256"] = sha256_b64(sha256)
                    headers["x-amz-checksum-sha256"] = params["ChecksumSHA256"]
                url = self.signer.generate_presigned_url(
                    "put_object", Params=params, ExpiresIn=self.ttl
                )
                return {"method": "PUT", "url": url, "headers": headers, "upload_id": None}
            plan = plan_parts(size, part)
            upload_id = self.s3.create_multipart_upload(
                Bucket=self.bucket, Key=key, ContentType=content_type
            )["UploadId"]
            return {
                "method": "MULTIPART",
                "upload_id": upload_id,
                "part_size": part,
                "parts": [
                    {
                        "part_number": number,
                        "offset": offset,
                        "length": length,
                        "url": self.signer.generate_presigned_url(
                            "upload_part",
                            Params={
                                "Bucket": self.bucket,
                                "Key": key,
                                "UploadId": upload_id,
                                "PartNumber": number,
                            },
                            ExpiresIn=self.ttl,
                        ),
                    }
                    for number, offset, length in plan
                ],
            }
        except BotoCoreError, ClientError:
            raise storage_unavailable() from None

    def complete_multipart(self, key: str, upload_id: str, parts: list[dict[str, Any]]) -> None:
        try:
            self.s3.complete_multipart_upload(
                Bucket=self.bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={
                    "Parts": [
                        {"ETag": p["etag"], "PartNumber": int(p["part_number"])}
                        for p in sorted(parts, key=lambda p: int(p["part_number"]))
                    ]
                },
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"NoSuchUpload", "InvalidPart", "InvalidPartOrder", "EntityTooSmall"}:
                raise Problem(
                    409, "upload-incomplete", "The upload is missing parts or a part is wrong"
                ) from None
            raise storage_unavailable() from None
        except BotoCoreError:
            raise storage_unavailable() from None

    def abort_multipart(self, key: str, upload_id: str) -> None:
        try:
            self.s3.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id)
        except BotoCoreError, ClientError:
            pass

    def head(self, key: str) -> ObjectInfo | None:
        try:
            head = self.s3.head_object(Bucket=self.bucket, Key=key, ChecksumMode="ENABLED")
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code", "") in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise storage_unavailable() from None
        except BotoCoreError:
            raise storage_unavailable() from None
        checksum = head.get("ChecksumSHA256")
        return ObjectInfo(
            int(head["ContentLength"]),
            str(head.get("ETag", "")).strip('"'),
            sha256_hex(checksum) if checksum and "-" not in checksum else None,
        )

    # ------------------------------------------------------------------ downloads and copies
    def sign_get(
        self, key: str, *, content_type: str | None = None, expires_in: int | None = None
    ) -> str:
        params: dict[str, Any] = {"Bucket": self.bucket, "Key": key}
        if content_type:
            params["ResponseContentType"] = content_type
        try:
            return self.signer.generate_presigned_url(
                "get_object", Params=params, ExpiresIn=min(expires_in or self.ttl, 7 * 24 * 3600)
            )
        except BotoCoreError, ClientError:
            raise storage_unavailable() from None

    def sign_put(self, key: str, size: int, content_type: str, sha256: str = "") -> dict[str, Any]:
        return self.begin_upload(key, min(size, SINGLE_PUT_MAX), content_type, sha256)

    def copy(self, source: str, destination: str) -> None:
        """Server-side copy inside the bucket (multipart for large objects, no bytes through us)."""
        try:
            self.s3.copy({"Bucket": self.bucket, "Key": source}, self.bucket, destination)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code", "") in {"404", "NoSuchKey", "NotFound"}:
                raise Problem(409, "upload-incomplete", "The source object is missing") from None
            raise storage_unavailable() from None
        except BotoCoreError:
            raise storage_unavailable() from None

    def delete_prefix(self, prefix: str) -> int:
        """Remove every object under a storage prefix (a whole volume); returns the count."""
        removed = 0
        try:
            for page in self.s3.get_paginator("list_objects_v2").paginate(
                Bucket=self.bucket, Prefix=prefix
            ):
                keys = [item["Key"] for item in page.get("Contents", [])]
                self.delete(keys)
                removed += len(keys)
        except BotoCoreError, ClientError:
            raise storage_unavailable() from None
        return removed

    def delete(self, keys: list[str]) -> None:
        for start in range(0, len(keys), 1000):
            chunk = keys[start : start + 1000]
            try:
                self.s3.delete_objects(
                    Bucket=self.bucket,
                    Delete={"Objects": [{"Key": k} for k in chunk], "Quiet": True},
                )
            except BotoCoreError, ClientError:
                raise storage_unavailable() from None
