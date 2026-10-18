"""What the platform knows about an uploaded file, kept beside the file itself.

There is no files table. Bytes, versions, quotas and ownership are the file store's (Substrate Data's Files domain in production);
what the platform adds to an upload -- its display name, the conversation it was attached to, whether the document was read and
filed -- is one small JSON object per file in the owner's own folder:

    tenants/{tenant}/users/{owner}/.files/ids/{file_id}.json          the record
    tenants/{tenant}/users/{owner}/.files/threads/{thread_id}/{id}    an empty marker: this file is attached to that conversation

Because the records live under ``users/{owner}/``, the data service's ownership rule is the access check: another user's token
cannot read, list or overwrite them, so a file id of someone else's is simply not found. A record whose file has been deleted (by
path, by erasure, by the user) is not live; nothing has to be kept in step.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Any

from substrate.types.errors import ObjectNotFoundError
from substrate.workspace.layout import user_prefix

logger = logging.getLogger(__name__)

INDEX_DIR = ".files"
_DATETIMES = (
    "created_at",
    "uploaded_at",
    "staged_at",
    "rag_ingested_at",
    "extracted_at",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class FileRecord:
    id: uuid.UUID
    tenant_id: str
    owner: str
    object_key: str
    original_name: str
    content_type: str = "application/octet-stream"
    size_bytes: int = 0
    checksum_sha256: str = ""
    thread_id: uuid.UUID | None = None
    scope: str = "uploads"
    page_count: int | None = None
    # None until the bytes have arrived (a direct upload is started, then completed). ``upload_id`` is the data service's handle on
    # the started upload, kept until it is completed.
    uploaded_at: datetime | None = None
    upload_id: str | None = None
    # Eager staging: the document was read once and filed in the conversation's documents.
    staged_at: datetime | None = None
    staging_error: str | None = None
    rag_ingested_at: datetime | None = None
    extracted_text: str | None = None
    extracted_at: datetime | None = None
    extraction_engine: str | None = None
    created_at: datetime = field(default_factory=_now)

    def to_json(self) -> bytes:
        body = asdict(self)
        body["id"] = str(self.id)
        body["thread_id"] = str(self.thread_id) if self.thread_id else None
        for name in _DATETIMES:
            value = getattr(self, name)
            body[name] = value.isoformat() if value else None
        # Compact: the record is bytes in the owner's quota too, so absent fields are left out.
        return json.dumps(
            {k: v for k, v in body.items() if v is not None}, separators=(",", ":")
        ).encode()

    @classmethod
    def from_json(cls, raw: bytes) -> FileRecord:
        body: dict[str, Any] = json.loads(raw)
        known = {f.name for f in fields(cls)}
        body = {k: v for k, v in body.items() if k in known}
        body["id"] = uuid.UUID(body["id"])
        body["thread_id"] = (
            uuid.UUID(body["thread_id"]) if body.get("thread_id") else None
        )
        for name in _DATETIMES:
            if body.get(name):
                body[name] = datetime.fromisoformat(body[name])
        return cls(**body)


def key_owner(key: str) -> str | None:
    """The user whose folder an object key is in (``tenants/{t}/users/{u}/...``), or None."""
    parts = key.split("/")
    if len(parts) >= 4 and parts[0] == "tenants" and parts[2] == "users" and parts[3]:
        return parts[3]
    return None


class FileIndex:
    """The records of one user's files, over that user's file store."""

    def __init__(self, store: Any, tenant_id: str, owner: str) -> None:
        self._store = store
        self.tenant_id = tenant_id
        self.owner = owner
        self._root = f"{user_prefix(tenant_id, owner)}/{INDEX_DIR}"

    def _record_key(self, file_id: uuid.UUID | str) -> str:
        return f"{self._root}/ids/{file_id}.json"

    def _marker_key(self, thread_id: uuid.UUID | str, file_id: uuid.UUID | str) -> str:
        return f"{self._root}/threads/{thread_id}/{file_id}"

    async def save(self, record: FileRecord) -> None:
        await self._store.upload(
            self._record_key(record.id),
            record.to_json(),
            content_type="application/json",
        )
        if record.thread_id is not None:
            await self._store.upload(
                self._marker_key(record.thread_id, record.id),
                b"",
                content_type="application/octet-stream",
            )

    async def get(self, file_id: uuid.UUID | str) -> FileRecord | None:
        """The record, whether or not its bytes are there yet. None if there is no such record for this user."""
        try:
            return FileRecord.from_json(
                await self._store.download(self._record_key(file_id))
            )
        except (ObjectNotFoundError, FileNotFoundError, KeyError):
            return None
        except (ValueError, TypeError) as exc:  # a corrupt record is as good as none
            logger.warning("Unreadable file record %s: %s", file_id, exc)
            return None

    async def live(self, file_id: uuid.UUID | str) -> FileRecord | None:
        """The record if its bytes have arrived and are still there."""
        record = await self.get(file_id)
        if record is None or record.uploaded_at is None:
            return None
        return record if await self._store.exists(record.object_key) else None

    async def for_thread(
        self, thread_id: uuid.UUID | str, extra: list[uuid.UUID] | None = None
    ) -> list[FileRecord]:
        """Live files attached to a conversation, plus the named ones (a newly attached file may be from another)."""
        ids: list[str] = []
        prefix = self._marker_key(thread_id, "")
        for key, _size, _mtime in await self._store.list_prefix(prefix):
            ids.append(key.rsplit("/", 1)[-1])
        ids += [str(i) for i in extra or []]
        seen: set[str] = set()
        found: list[FileRecord] = []
        for file_id in ids:
            if file_id in seen:
                continue
            seen.add(file_id)
            record = await self.live(file_id)
            if record is not None:
                found.append(record)
        found.sort(key=lambda r: r.created_at)
        return found

    async def remove(self, record: FileRecord) -> None:
        await self._store.delete(self._record_key(record.id))
        if record.thread_id is not None:
            await self._store.delete(self._marker_key(record.thread_id, record.id))
