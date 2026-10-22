"""Plain data returned by the client. No third-party types: the core needs only httpx."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class FileInfo:
    """A file's current state in a volume."""

    path: str
    size: int
    content_type: str
    version: int
    etag: str
    sha256: str | None = None
    sha256_verified: bool = False
    created_by: str = ""
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_api(cls, body: dict[str, Any]) -> FileInfo:
        return cls(
            path=body["path"],
            size=int(body["size"]),
            content_type=body["content_type"],
            version=int(body["version"]),
            etag=body.get("etag", f'"{body["version"]}"'),
            sha256=body.get("sha256"),
            sha256_verified=bool(body.get("sha256_verified")),
            created_by=body.get("created_by", ""),
            created_at=body.get("created_at", ""),
            updated_at=body.get("updated_at", ""),
        )

    @property
    def mtime(self) -> float:
        """When the current version was written, as seconds since the epoch."""
        return datetime.fromisoformat(self.updated_at).timestamp() if self.updated_at else 0.0


@dataclass(frozen=True)
class FileVersion:
    version: int
    size: int
    content_type: str
    current: bool
    purged: bool
    sha256: str | None = None
    created_by: str = ""
    created_at: str = ""

    @classmethod
    def from_api(cls, body: dict[str, Any]) -> FileVersion:
        return cls(
            version=int(body["version"]),
            size=int(body["size"]),
            content_type=body["content_type"],
            current=bool(body["current"]),
            purged=bool(body["purged"]),
            sha256=body.get("sha256"),
            created_by=body.get("created_by", ""),
            created_at=body.get("created_at", ""),
        )


@dataclass(frozen=True)
class Listing:
    """One level of a delimited listing."""

    files: list[FileInfo] = field(default_factory=list)
    directories: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Usage:
    volume: str
    prefix: str
    files: int
    bytes: int
    volume_bytes: int
    retained_bytes: int
    quota_bytes: int | None

    @classmethod
    def from_api(cls, body: dict[str, Any]) -> Usage:
        return cls(
            volume=body["volume"],
            prefix=body["prefix"],
            files=int(body["files"]),
            bytes=int(body["bytes"]),
            volume_bytes=int(body["volume_bytes"]),
            retained_bytes=int(body["retained_bytes"]),
            quota_bytes=body.get("quota_bytes"),
        )


@dataclass(frozen=True)
class QueryResult:
    """A JSON result: column names and types, and rows as lists (no Arrow needed)."""

    columns: list[dict[str, str]]
    rows: list[list[Any]]
    truncated: bool = False
    statement_id: str | None = None

    @property
    def names(self) -> list[str]:
        return [c["name"] for c in self.columns]

    def records(self) -> list[dict[str, Any]]:
        return [dict(zip(self.names, row, strict=True)) for row in self.rows]

    def __len__(self) -> int:
        return len(self.rows)
