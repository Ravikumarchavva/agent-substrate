"""The Files domain: uploads, versions, signing and sweeping. Routes authorize; this does the work."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from loamz.data.server.features.files.paths import normalize_path
from loamz.data.server.features.files.policy import OwnerPrefixPolicy, PathPolicy
from loamz.data.server.features.files.quota import check_quota
from loamz.data.server.features.files.repo import FileCatalog
from loamz.data.server.features.files.storage import SINGLE_PUT_MAX, VolumeStorage
from loamz.data.server.kernel.container import Container
from loamz.data.server.kernel.errors import UploadIncompleteError, not_found
from loamz.data.server.kernel.metadata import CatalogStore
from loamz.data.server.kernel.principal import Principal
from loamz.data.server.kernel.settings import Settings
from loamz.data.storage.errors import (
    ConflictError,
    InvalidRequestError,
    LoamzError,
    NotFoundError,
    PreconditionFailedError,
    QuotaExceededError,
    RateLimitedError,
    ServiceUnavailableError,
    http_status,
)

logger = logging.getLogger(__name__)
MAX_PENDING_PER_PRINCIPAL = 1000
MAX_SIGN_ITEMS = 200
DEFAULT_CONTENT_TYPE = "application/octet-stream"


def path_hash(path: str) -> str:
    """What the audit log records instead of a file name."""
    return hashlib.sha256(path.encode()).hexdigest()[:16]


def public_file(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": row["path"],
        "size": int(row["size"]),
        "sha256": row["sha256"] or None,
        "sha256_verified": bool(row["sha256_verified"]),
        "content_type": row["content_type"],
        "version": int(row["version"]),
        "etag": f'"{row["version"]}"',
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def public_version(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": int(row["version"]),
        "size": int(row["size"]),
        "sha256": row["sha256"] or None,
        "sha256_verified": bool(row["sha256_verified"]),
        "content_type": row["content_type"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "current": row["current"],
        "purged": row["purged_at"] is not None,
    }


def problem_body(exc: LoamzError) -> dict[str, Any]:
    return {"type": exc.code, "status": http_status(exc), "detail": str(exc), **exc.extensions()}


class FilesService:
    def __init__(
        self,
        settings: Settings,
        store: CatalogStore,
        storage: VolumeStorage | None = None,
        policy: PathPolicy | None = None,
    ):
        self.settings = settings
        self.store = store
        self.catalog = FileCatalog(store)
        self.storage = storage or VolumeStorage(settings)
        self.policy: PathPolicy = policy or OwnerPrefixPolicy()

    def expiry(self) -> str:
        return (
            datetime.now(UTC) + timedelta(hours=self.settings.upload_session_ttl_hours)
        ).isoformat()

    # ------------------------------------------------------------------ uploads
    def _check_size(self, size: int) -> None:
        if size > self.settings.files_max_file_bytes:
            raise QuotaExceededError("The file exceeds the maximum file size")

    def _fail_fast(
        self, volume_id: str, path: str, size: int, if_match: int | None, if_none_match: bool
    ) -> None:
        head = self.catalog.head(volume_id, path)
        if if_none_match and head is not None:
            raise PreconditionFailedError("The file already exists")
        if if_match is not None and (head is None or head["version"] != if_match):
            raise PreconditionFailedError("The file changed; reload and retry")
        check_quota(
            self.catalog.live_bytes(volume_id),
            size,
            self.catalog.quota(volume_id),
            head["size"] if head else 0,
        )

    def _pending_allowed(self, caller: Principal, extra: int = 1) -> None:
        if self.catalog.pending_count(caller.id) + extra > MAX_PENDING_PER_PRINCIPAL:
            raise RateLimitedError(
                "Too many unfinished uploads; finish or wait for them to expire", retry_after=60
            )

    def initiate_upload(
        self,
        volume: dict,
        caller: Principal,
        *,
        path: str,
        size: int,
        content_type: str | None,
        sha256: str,
        if_match: int | None,
        if_none_match: bool,
    ) -> dict[str, Any]:
        self._check_size(size)
        self._fail_fast(volume["id"], path, size, if_match, if_none_match)
        self._pending_allowed(caller)
        content_type = content_type or DEFAULT_CONTENT_TYPE
        key = self.storage.object_key(volume["id"])
        plan = self.storage.begin_upload(key, size, content_type, sha256)
        row = self.catalog.create_upload(
            {
                "volume_id": volume["id"],
                "path": path,
                "object_key": key,
                "upload_id": plan.pop("upload_id"),
                "size": size,
                "content_type": content_type,
                "sha256": sha256,
                "created_by": caller.id,
                "expires_at": self.expiry(),
            }
        )
        return {"upload_id": row["id"], "path": path, "expires_at": row["expires_at"], **plan}

    def _claim(self, row: dict, caller: Principal) -> None:
        if row["created_by"] != caller.id and not caller.is_admin:
            raise not_found("Upload")
        if row["expires_at"] < datetime.now(UTC).isoformat() and row["state"] != "completed":
            raise NotFoundError("The upload expired; start again")
        if not self.catalog.move_upload(row["id"], "pending", "completing"):
            raise ConflictError(f"The upload is already {self.catalog.upload(row['id'])['state']}")

    def _verified_object(self, row: dict, parts: list[dict]) -> tuple[str, bool]:
        """Finish the upload if multipart, then check what arrived: size always, SHA-256 when the
        store kept one. Returns (sha256, verified). Anything wrong deletes the object."""
        key = row["object_key"]
        if row["upload_id"]:
            if not parts:
                raise InvalidRequestError("Part ETags are required to complete")
            self.storage.complete_multipart(key, row["upload_id"], parts)
        info = self.storage.head(key)
        if info is None:
            raise UploadIncompleteError("The upload is missing or incomplete")
        if info.size != row["size"]:
            self.storage.delete([key])
            raise ConflictError(f"{info.size} bytes arrived, {row['size']} were declared")
        declared = row["sha256"]
        if declared and info.sha256 is not None and info.sha256 != declared:
            self.storage.delete([key])
            raise ConflictError("The uploaded bytes do not match the SHA-256")
        return (declared or info.sha256 or ""), info.sha256 is not None

    def complete_upload(
        self,
        volume: dict,
        caller: Principal,
        upload_id: str,
        parts: list[dict],
        *,
        if_match: int | None,
        if_none_match: bool,
    ) -> dict[str, Any]:
        row = self.catalog.upload(upload_id)
        if (
            row["volume_id"] != volume["id"]
            or row["created_by"] != caller.id
            and not caller.is_admin
        ):
            raise not_found("Upload")
        self.policy.authorize(caller, row["path"], "write")
        if row["state"] == "completed":  # a retried completion is not an error
            head = self.catalog.head(volume["id"], row["path"])
            if head is not None:
                return head
        self._claim(row, caller)
        try:
            sha256, verified = self._verified_object(row, parts)
            head = self.catalog.commit_version(
                volume["id"],
                row["path"],
                {
                    "object_key": row["object_key"],
                    "size": row["size"],
                    "sha256": sha256,
                    "sha256_verified": verified,
                    "content_type": row["content_type"],
                },
                caller.id,
                if_match=if_match,
                if_none_match=if_none_match,
            )
        except LoamzError as exc:
            if isinstance(exc, ServiceUnavailableError | UploadIncompleteError):
                self.catalog.move_upload(upload_id, "completing", "pending")  # try again later
            else:
                self.catalog.move_upload(upload_id, "completing", "failed")
                self._discard(row)
            raise
        self.catalog.move_upload(upload_id, "completing", "completed")
        return head

    def _discard(self, row: dict) -> None:
        with contextlib.suppress(LoamzError):
            self.storage.delete([row["object_key"]])

    # ------------------------------------------------------------------ batch signing
    def sign(
        self, volume: dict, caller: Principal, items: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Presigned URLs for many small files in one call; one bad item does not fail the rest."""
        results = []
        if any(i["action"] == "put" for i in items):
            self._pending_allowed(caller, sum(1 for i in items if i["action"] == "put"))
        for item in items:
            try:
                path = normalize_path(item["path"])
                if item["action"] == "get":
                    self.policy.authorize(caller, path, "read")
                    obj = self.catalog.current_object(volume["id"], path)
                    results.append(
                        {
                            "path": path,
                            "action": "get",
                            "url": self.storage.sign_get(
                                obj["object_key"], content_type=obj["content_type"]
                            ),
                            "size": int(obj["size"]),
                            "version": int(obj["version"]),
                        }
                    )
                    continue
                self.policy.authorize(caller, path, "write")
                size = item.get("size")
                if size is None or size > SINGLE_PUT_MAX:
                    raise InvalidRequestError("A put needs a size of at most 64 MiB")
                started = self.initiate_upload(
                    volume,
                    caller,
                    path=path,
                    size=size,
                    content_type=item.get("content_type"),
                    sha256=item.get("sha256") or "",
                    if_match=None,
                    if_none_match=False,
                )
                results.append({"action": "put", **started})
            except LoamzError as exc:
                results.append(
                    {"path": item["path"], "action": item["action"], "error": problem_body(exc)}
                )
        return results

    def download_url(
        self, volume: dict, path: str, version: int | None, expires_in: int | None
    ) -> dict[str, Any]:
        obj = self.catalog.current_object(volume["id"], path, version)
        ttl = min(expires_in or self.settings.files_url_ttl_seconds, 7 * 24 * 3600)
        return {
            "url": self.storage.sign_get(
                obj["object_key"], content_type=obj["content_type"], expires_in=ttl
            ),
            "expires_at": (datetime.now(UTC) + timedelta(seconds=ttl)).isoformat(),
            "version": int(obj["version"]),
            "size": int(obj["size"]),
            "content_type": obj["content_type"],
        }

    # ------------------------------------------------------------------ upload sessions
    def create_session(
        self, caller: Principal, *, size: int, content_type: str | None, sha256: str
    ) -> dict[str, Any]:
        self._check_size(size)
        self._pending_allowed(caller)
        content_type = content_type or DEFAULT_CONTENT_TYPE
        key = self.storage.session_key(uuid4().hex)
        plan = self.storage.begin_upload(key, size, content_type, sha256)
        row = self.catalog.create_upload(
            {
                "volume_id": None,
                "path": None,
                "object_key": key,
                "upload_id": plan.pop("upload_id"),
                "size": size,
                "content_type": content_type,
                "sha256": sha256,
                "created_by": caller.id,
                "expires_at": self.expiry(),
            }
        )
        return {"id": row["id"], "expires_at": row["expires_at"], **plan}

    def session(self, session_id: str, caller: Principal) -> dict:
        row = self.catalog.upload(session_id)
        if row["volume_id"] is not None or (row["created_by"] != caller.id and not caller.is_admin):
            raise not_found("Upload session")
        return row

    def promote(
        self,
        session_id: str,
        volume: dict,
        caller: Principal,
        *,
        path: str,
        parts: list[dict],
        if_match: int | None,
        if_none_match: bool,
    ) -> dict[str, Any]:
        """Copy a finished session upload into a volume path (server-side) and drop the session."""
        row = self.session(session_id, caller)
        self.policy.authorize(caller, path, "write")
        self._claim(row, caller)
        new_key = self.storage.object_key(volume["id"])
        copied = False
        try:
            sha256, verified = self._verified_object(row, parts)
            self._fail_fast(volume["id"], path, row["size"], if_match, if_none_match)
            self.storage.copy(row["object_key"], new_key)
            copied = True
            head = self.catalog.commit_version(
                volume["id"],
                path,
                {
                    "object_key": new_key,
                    "size": row["size"],
                    "sha256": sha256,
                    "sha256_verified": verified,
                    "content_type": row["content_type"],
                },
                caller.id,
                if_match=if_match,
                if_none_match=if_none_match,
            )
        except LoamzError as exc:
            if copied:
                self._discard({"object_key": new_key})
            if isinstance(exc, ServiceUnavailableError | UploadIncompleteError):
                self.catalog.move_upload(session_id, "completing", "pending")
            else:
                self.catalog.move_upload(session_id, "completing", "failed")
                self._discard(row)
            raise
        self.catalog.move_upload(session_id, "completing", "promoted")
        self._discard(row)
        return head

    # ------------------------------------------------------------------ sweeping
    def sweep(self) -> dict[str, int]:
        """Remove expired pending uploads, purge old versions, drop files of unregistered volumes."""
        done = {"uploads": 0, "versions": 0, "volumes": 0}
        for row in self.catalog.expired_uploads():
            if row["state"] in {"pending", "completing", "failed"}:
                if row["upload_id"]:
                    self.storage.abort_multipart(row["object_key"], row["upload_id"])
                self.storage.delete([row["object_key"]])
            self.catalog.forget_upload(row["id"])
            done["uploads"] += 1
        garbage = self.catalog.purge_versions(self.settings.files_version_retention_days)
        if garbage:
            self.storage.delete(garbage)
        done["versions"] = len(garbage)
        for volume_id in self.catalog.orphaned_volumes():
            self.storage.delete_prefix(f"volumes/{volume_id}/")
            self.catalog.drop_volume(volume_id)
            done["volumes"] += 1
        return done


async def sweep_files(service: FilesService) -> None:
    """Background loop started by the app: expiry and purging, forever, never raising."""
    while True:
        try:
            done = await asyncio.to_thread(service.sweep)
            if any(done.values()):
                logger.info("Files sweep removed %s", done)
        except Exception:  # noqa: BLE001 - a failed sweep must never take the API down
            logger.exception("Files sweep failed")
        await asyncio.sleep(service.settings.files_sweep_interval_seconds)


def files_service(container: Container) -> FilesService:
    """Provider: governed files in the volumes."""
    return FilesService(container.settings, container.store)
