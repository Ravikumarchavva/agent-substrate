"""Document model for stored uploads and generated assets.

All persisted binary/text assets should be represented as ``Document`` or one of
its subclasses before being written to the pluggable ``FileStore`` layer.
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field, replace
from typing import Any, AsyncIterator, ClassVar, Mapping

from raavan.core.storage.base import FileRef, FileStore
from raavan.core.storage.tenant import FileScope, TenantContext

_TEXT_MIMES = {
    "text/plain",
    "text/markdown",
    "text/csv",
    "text/html",
    "text/css",
    "text/javascript",
    "application/javascript",
    "application/json",
    "application/xml",
    "text/xml",
}
_TEXT_EXTENSIONS = {
    "txt",
    "md",
    "csv",
    "html",
    "htm",
    "css",
    "js",
    "ts",
    "tsx",
    "jsx",
    "json",
    "xml",
    "yaml",
    "yml",
    "toml",
    "ini",
    "log",
    "sql",
    "py",
}
_SPREADSHEET_MIMES = {
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
_SPREADSHEET_EXTENSIONS = {"xls", "xlsx", "ods"}
_ARCHIVE_MIMES = {
    "application/zip",
    "application/x-tar",
    "application/gzip",
    "application/x-7z-compressed",
    "application/x-rar-compressed",
}
_ARCHIVE_EXTENSIONS = {"zip", "tar", "gz", "tgz", "bz2", "xz", "7z", "rar"}
_EXTENSION_CONTENT_TYPES = {
    "csv": "text/csv",
    "json": "application/json",
    "md": "text/markdown",
    "pdf": "application/pdf",
    "txt": "text/plain",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "zip": "application/zip",
}


@dataclass(frozen=True, slots=True)
class Document:
    """Base document descriptor for any stored asset."""

    name: str
    content_type: str = "application/octet-stream"
    size_bytes: int = 0
    extension: str = ""
    object_key: str | None = None
    checksum_sha256: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    document_type: ClassVar[str] = "document"

    def __post_init__(self) -> None:
        name = _basename(self.name)
        content_type = _normalise_content_type(name, self.content_type)
        extension = (self.extension or _extension_from_name(name)).lower()
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "content_type", content_type)
        object.__setattr__(self, "extension", extension)
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def document_class(self) -> str:
        return type(self).__name__

    def descriptor(self) -> dict[str, Any]:
        """Return JSON-serialisable document metadata for DB/API storage."""
        descriptor = dict(self.metadata)
        descriptor.update(
            {
                "document_type": self.document_type,
                "document_class": self.document_class,
                "content_type": self.content_type,
            }
        )
        if self.extension:
            descriptor["extension"] = self.extension
        if self.object_key:
            descriptor["object_key"] = self.object_key
        if self.checksum_sha256:
            descriptor["checksum_sha256"] = self.checksum_sha256
        return descriptor

    def storage_metadata(self) -> dict[str, str]:
        """Return scalar metadata safe to persist on object stores like S3."""
        metadata = {
            "document_type": self.document_type,
            "document_class": self.document_class,
            "content_type": self.content_type,
        }
        if self.extension:
            metadata["extension"] = self.extension
        return metadata

    def with_storage(self, ref: FileRef) -> Document:
        """Bind the document descriptor to a stored object reference."""
        return replace(
            self,
            object_key=ref.object_key,
            size_bytes=ref.size_bytes,
            checksum_sha256=ref.checksum_sha256,
        )


@dataclass(frozen=True, slots=True)
class TextDocument(Document):
    document_type: ClassVar[str] = "text"


@dataclass(frozen=True, slots=True)
class JsonDocument(Document):
    document_type: ClassVar[str] = "json"


@dataclass(frozen=True, slots=True)
class SpreadsheetDocument(Document):
    document_type: ClassVar[str] = "spreadsheet"


@dataclass(frozen=True, slots=True)
class PdfDocument(Document):
    document_type: ClassVar[str] = "pdf"


@dataclass(frozen=True, slots=True)
class ImageDocument(Document):
    document_type: ClassVar[str] = "image"


@dataclass(frozen=True, slots=True)
class AudioDocument(Document):
    document_type: ClassVar[str] = "audio"


@dataclass(frozen=True, slots=True)
class VideoDocument(Document):
    document_type: ClassVar[str] = "video"


@dataclass(frozen=True, slots=True)
class ArchiveDocument(Document):
    document_type: ClassVar[str] = "archive"


@dataclass(frozen=True, slots=True)
class BinaryDocument(Document):
    document_type: ClassVar[str] = "file"


def create_document(
    *,
    name: str,
    content_type: str | None = None,
    size_bytes: int = 0,
    metadata: Mapping[str, Any] | None = None,
) -> Document:
    """Infer the most specific document subtype for an asset."""
    normalised_name = _basename(name)
    normalised_type = _normalise_content_type(normalised_name, content_type)
    extension = _extension_from_name(normalised_name)
    document_cls = _document_class_for(normalised_type, extension)
    return document_cls(
        name=normalised_name,
        content_type=normalised_type,
        size_bytes=size_bytes,
        extension=extension,
        metadata=dict(metadata or {}),
    )


async def store_document(
    store: FileStore,
    *,
    tenant: TenantContext,
    name: str,
    content: bytes | AsyncIterator[bytes],
    content_type: str | None = None,
    scope: FileScope = FileScope.UPLOADS,
    metadata: Mapping[str, Any] | None = None,
) -> Document:
    """Persist a document into the configured FileStore and return it."""
    initial_size = len(content) if isinstance(content, bytes) else 0
    document = create_document(
        name=name,
        content_type=content_type,
        size_bytes=initial_size,
        metadata=metadata,
    )
    ref = await store.put(
        tenant.key(document.name, scope),
        content,
        content_type=document.content_type,
        metadata=document.storage_metadata(),
    )
    return document.with_storage(ref)


def _document_class_for(content_type: str, extension: str) -> type[Document]:
    if content_type.startswith("image/"):
        return ImageDocument
    if content_type.startswith("audio/"):
        return AudioDocument
    if content_type.startswith("video/"):
        return VideoDocument
    if content_type == "application/pdf" or extension == "pdf":
        return PdfDocument
    if content_type in _SPREADSHEET_MIMES or extension in _SPREADSHEET_EXTENSIONS:
        return SpreadsheetDocument
    if content_type == "application/json" or extension == "json":
        return JsonDocument
    if content_type in _TEXT_MIMES or extension in _TEXT_EXTENSIONS:
        return TextDocument
    if content_type in _ARCHIVE_MIMES or extension in _ARCHIVE_EXTENSIONS:
        return ArchiveDocument
    return BinaryDocument


def _normalise_content_type(name: str, content_type: str | None) -> str:
    candidate = (content_type or "").strip().lower()
    if not candidate or candidate == "application/octet-stream":
        guessed, _ = mimetypes.guess_type(name)
        if guessed:
            return guessed.lower()
        extension = _extension_from_name(name)
        if extension in _EXTENSION_CONTENT_TYPES:
            return _EXTENSION_CONTENT_TYPES[extension]
    return candidate or "application/octet-stream"


def _basename(name: str) -> str:
    basename = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].strip()
    return basename or "unnamed"


def _extension_from_name(name: str) -> str:
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()
