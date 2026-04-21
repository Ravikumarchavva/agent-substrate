"""Pluggable file-storage layer.

Provides a backend-agnostic ``FileStore`` ABC with drivers for local
filesystem, S3-compatible object stores, and an optional encryption wrapper.

Public surface::

    from ravi.core.storage import (
        FileStore,          # ABC
        FileRef,            # returned by put / get
        TenantContext,      # org/user/thread path builder
        LocalFileStore,     # local-disk driver
        EncryptedFileStore, # envelope-encryption decorator
    )

    # S3-compatible driver lives in integrations:
    from ravi.integrations.storage import S3FileStore
"""

from __future__ import annotations

from ravi.core.storage.base import FileRef, FileStore
from ravi.core.storage.document import (
    ArchiveDocument,
    AudioDocument,
    BinaryDocument,
    Document,
    ImageDocument,
    JsonDocument,
    PdfDocument,
    SpreadsheetDocument,
    TextDocument,
    VideoDocument,
    create_document,
    store_document,
)
from ravi.core.storage.tenant import FileScope, TenantContext
from ravi.core.storage.local import LocalFileStore
from ravi.core.storage.encrypted import (
    EncryptedFileStore,
    KeyProvider,
    LocalKeyProvider,
)

__all__ = [
    "FileRef",
    "FileStore",
    "Document",
    "TextDocument",
    "JsonDocument",
    "SpreadsheetDocument",
    "PdfDocument",
    "ImageDocument",
    "AudioDocument",
    "VideoDocument",
    "ArchiveDocument",
    "BinaryDocument",
    "create_document",
    "store_document",
    "FileScope",
    "TenantContext",
    "LocalFileStore",
    "EncryptedFileStore",
    "KeyProvider",
    "LocalKeyProvider",
]
