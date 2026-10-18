"""Short-lived, path-scoped storage credentials for statements (credential vending).

Without this, every SQL worker holds one long-lived key that reads the whole warehouse, so a
compromised worker can read everything. With it, the control plane asks the object store's STS for
credentials that can only `GetObject` under the table folders one statement was planned against,
for 15 minutes, and hands them to the worker inside the signed ticket. Workers then need no storage
key for reads at all. The session policy is the enforcement: it is evaluated by the object store,
not by this code.

Works with any S3-compatible STS (AWS, SeaweedFS, MinIO, Ceph): `AssumeRole` with an inline
session policy. Batch jobs that write keep their own key (they must write); only statements are
vended.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from loamz.data.server.kernel.errors import unavailable
from loamz.data.server.kernel.settings import Settings
from loamz.data.storage.errors import InvalidRequestError

MAX_PREFIXES = 25  # a session policy is limited to a couple of kilobytes
REFRESH_MARGIN = 300  # reuse credentials until fewer than this many seconds remain
MAX_CACHED = 512


@dataclass(frozen=True)
class Vended:
    key_id: str
    secret: str
    token: str
    expires: float  # epoch seconds
    scopes: tuple[str, ...]  # s3://bucket/prefix/ the credentials are limited to

    def claim(self) -> dict[str, object]:
        return {
            "key_id": self.key_id,
            "secret": self.secret,
            "token": self.token,
            "expires": self.expires,
            "scopes": list(self.scopes),
        }


def table_prefix(files: list[str]) -> str | None:
    """The folder holding a table's data files, as `s3://bucket/path/to/table/`."""
    if not files:
        return None
    first = files[0]
    if "/data/" in first:
        return first.split("/data/", 1)[0] + "/"
    return first.rsplit("/", 1)[0] + "/"


class CredentialVendor:
    def __init__(self, settings: Settings, client=None):
        self.settings = settings
        self.role = settings.storage_sts_role_arn
        self.bucket = settings.warehouse
        self.client = client or boto3.client(
            "sts",
            endpoint_url=settings.storage_sts_endpoint or settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key_id.get_secret_value(),
            aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
            region_name=settings.s3_region,
            config=Config(retries={"max_attempts": 2}, connect_timeout=3, read_timeout=5),
        )
        self.lock = threading.Lock()
        self.cache: dict[tuple[str, ...], Vended] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.role)

    def for_prefixes(self, prefixes: list[str]) -> Vended | None:
        """Credentials that read only under these table folders (None when vending is off)."""
        if not self.enabled or not prefixes:
            return None
        scopes = tuple(sorted(set(prefixes)))
        if len(scopes) > MAX_PREFIXES:
            raise InvalidRequestError(
                f"A statement may read at most {MAX_PREFIXES} tables when storage credentials "
                "are vended"
            )
        for scope in scopes:
            if not scope.startswith(f"s3://{self.bucket}/"):
                raise RuntimeError("A planned file is outside the warehouse")
        with self.lock:
            cached = self.cache.get(scopes)
        if cached and cached.expires - time.time() > REFRESH_MARGIN:
            return cached
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObject"],
                    "Resource": [
                        f"arn:aws:s3:::{self.bucket}/{urlsplit(scope).path.lstrip('/')}*"
                        for scope in scopes
                    ],
                }
            ],
        }
        try:
            response = self.client.assume_role(
                RoleArn=self.role,
                RoleSessionName="loamz-statement",
                DurationSeconds=self.settings.storage_sts_session_seconds,
                Policy=json.dumps(policy, separators=(",", ":")),
            )["Credentials"]
        except ClientError, BotoCoreError:
            raise unavailable("Storage credentials could not be issued") from None
        vended = Vended(
            key_id=response["AccessKeyId"],
            secret=response["SecretAccessKey"],
            token=response["SessionToken"],
            expires=response["Expiration"].timestamp(),
            scopes=scopes,
        )
        with self.lock:
            if len(self.cache) >= MAX_CACHED:
                self.cache.clear()
            self.cache[scopes] = vended
        return vended
