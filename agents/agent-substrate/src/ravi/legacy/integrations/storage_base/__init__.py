"""ravi.kernel.storage — File-storage contract + document types.

Only the ``FileStore`` ABC, document types, and tenant scoping live here.
The ``LocalFileStore`` reference driver lives in
:mod:`ravi.fabric.storage.local` (L1). Encryption, the S3 driver, and the
factory live in :mod:`ravi.integrations.storage`.
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
    "FileScope",
    "TenantContext",
]
