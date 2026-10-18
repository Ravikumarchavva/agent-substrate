"""``DataUploadSessions``: bytes that have no home yet (``POST /api/v1/upload-sessions``).

An attachment staged in a composer before the message carrying it is sent used to sit on the agent host's disk
(``PendingFileStore``). Now it is an upload session on the data service: the bytes go straight to object storage, nothing
about them is on this host, and an unsent one expires by itself after 24 hours. Sending the message *promotes* the session:
the service copies it server-side into the volume path (grants, path policy, quota and preconditions apply) and drops it.
"""

from __future__ import annotations

from typing import Any

from substrate_data_client import DataError, FileInfo

from substrate.integrations.data._errors import raise_for
from substrate.integrations.data._keys import split_key
from substrate.integrations.data.files import DataFilesConnection, UploadTicket
from substrate.types.run import RunScope


class DataUploadSessions:
    """Pending uploads for one run's identity."""

    def __init__(self, connection: DataFilesConnection, scope: RunScope) -> None:
        self._connection = connection
        self.scope = scope

    async def create(
        self, size: int, *, content_type: str | None = None, sha256: str | None = None
    ) -> UploadTicket:
        """Start a session. ``ticket.key`` is empty and ``ticket.upload_id`` is the session id to ``promote`` later."""
        try:
            with self._connection.acting(self.scope):
                started = await self._connection.client.files.create_session(
                    size, content_type=content_type, sha256=sha256
                )
        except DataError as exc:
            raise_for(exc, "upload-session")
        return UploadTicket(
            key="",
            upload_id=started["id"],
            method=started["method"],
            url=started.get("url"),
            headers=dict(started.get("headers") or {}),
            parts=list(started.get("parts") or []),
            part_size=started.get("part_size"),
            expires_at=started.get("expires_at"),
        )

    async def put(
        self, ticket: UploadTicket, data: bytes
    ) -> list[dict[str, Any]] | None:
        """Send *data* to the ticket's URL(s) from this process (for small server-made content; browsers send their own).

        Returns the parts to pass to ``promote`` for a multipart ticket, else ``None``."""
        files = self._connection.client.files
        try:
            if ticket.method == "PUT" and ticket.url:
                await files.put_to_url(ticket.url, data, ticket.headers)
                return None
            done = []
            for part in ticket.parts:
                chunk = data[part["offset"] : part["offset"] + part["length"]]
                done.append(
                    {
                        "part_number": part["part_number"],
                        "etag": await files.put_to_url(part["url"], chunk),
                    }
                )
            return done
        except DataError as exc:
            raise_for(exc, "upload-session")

    async def promote(
        self,
        session_id: str,
        key: str,
        parts: list[dict[str, Any]] | None = None,
        *,
        if_none_match: bool = False,
    ) -> FileInfo:
        """Finish the session and file it at *key* (a ``FileStore`` key). The session is gone afterwards."""
        tenant, path = split_key(key)
        try:
            with self._connection.acting(self.scope):
                return await self._connection.client.files.promote_session(
                    session_id,
                    self._connection.volume_of(tenant),
                    path,
                    parts,
                    if_none_match=if_none_match,
                )
        except DataError as exc:
            raise_for(exc, key, tenant_id=tenant)


__all__ = ["DataUploadSessions"]
