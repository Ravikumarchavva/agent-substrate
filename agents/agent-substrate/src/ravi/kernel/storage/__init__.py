"""ravi.kernel.storage — File-storage contract + local reference driver.

Only the ``FileStore`` ABC, document types, and the local-disk driver
live here. Encryption, multi-tenant routing, S3 driver, and the factory
live in :mod:`ravi.extensions.storage` and :mod:`ravi.integrations.storage`.
"""

from __future__ import annotations

from ravi.kernel.storage.base import FileRef, FileStore
from ravi.kernel.storage.document import (
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
from ravi.kernel.storage.local import LocalFileStore
from ravi.kernel.storage.tenant import FileScope, TenantContext

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
    "LocalFileStore",
    "FileScope",
    "TenantContext",
]
