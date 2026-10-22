"""Direct-to-storage uploads: the control plane signs URLs, clients send bytes to S3 directly."""

from __future__ import annotations

import math
import re
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from loamz.engine.errors import InvalidRequestError, ServiceUnavailableError
from loamz.server.data.kernel.container import Container
from loamz.server.data.kernel.errors import UploadIncompleteError
from loamz.server.data.kernel.settings import Settings

URL_TTL_SECONDS = 3600
MAX_PARTS = 10_000


def safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", name.rsplit("/", 1)[-1])[:120].strip("._")
    return cleaned or "upload"


class Staging:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.bucket = settings.staging_bucket
        self.s3 = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key_id.get_secret_value(),
            aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{key}"

    def begin(self, key: str, size: int) -> dict[str, Any]:
        """One presigned PUT for small files, presigned multipart parts for large ones."""
        part = self.settings.upload_part_bytes
        try:
            if size <= part:
                url = self.s3.generate_presigned_url(
                    "put_object",
                    Params={"Bucket": self.bucket, "Key": key},
                    ExpiresIn=URL_TTL_SECONDS,
                )
                return {"key": key, "method": "PUT", "url": url}
            parts = math.ceil(size / part)
            if parts > MAX_PARTS:
                raise InvalidRequestError("File is too large for multipart upload")
            upload_id = self.s3.create_multipart_upload(Bucket=self.bucket, Key=key)["UploadId"]
            return {
                "key": key,
                "method": "MULTIPART",
                "upload_id": upload_id,
                "part_size": part,
                "parts": [
                    {
                        "part_number": number,
                        "url": self.s3.generate_presigned_url(
                            "upload_part",
                            Params={
                                "Bucket": self.bucket,
                                "Key": key,
                                "UploadId": upload_id,
                                "PartNumber": number,
                            },
                            ExpiresIn=URL_TTL_SECONDS,
                        ),
                    }
                    for number in range(1, parts + 1)
                ],
            }
        except BotoCoreError, ClientError:
            raise ServiceUnavailableError("Object storage is unavailable") from None

    def complete(self, key: str, upload_id: str | None, parts: list[dict[str, Any]]) -> int:
        """Finish a multipart upload (if any) and return the stored object's size."""
        try:
            if upload_id:
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
            return int(self.s3.head_object(Bucket=self.bucket, Key=key)["ContentLength"])
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"NoSuchKey", "404", "NoSuchUpload", "InvalidPart"}:
                raise UploadIncompleteError("The upload is missing or incomplete") from None
            raise ServiceUnavailableError("Object storage is unavailable") from None
        except BotoCoreError:
            raise ServiceUnavailableError("Object storage is unavailable") from None

    def remove(self, keys: list[str]) -> None:
        if not keys:
            return
        try:
            self.s3.delete_objects(
                Bucket=self.bucket, Delete={"Objects": [{"Key": k} for k in keys], "Quiet": True}
            )
        except BotoCoreError, ClientError:
            pass


def staging_area(container: Container) -> Staging:
    """Provider: the bucket uploads are staged in before they are imported."""
    return Staging(container.settings)
