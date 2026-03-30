"""Pluggable file-storage layer.

Provides a backend-agnostic ``FileStore`` ABC with drivers for local
filesystem, S3-compatible object stores, and an optional encryption wrapper.

Public surface::

    from raavan.core.storage import (
        FileStore,          # ABC
        FileRef,            # returned by put / get
        TenantContext,      # org/user/thread path builder
        LocalFileStore,     # local-disk driver
        EncryptedFileStore, # envelope-encryption decorator
    )

    # S3-compatible driver lives in integrations:
    from raavan.integrations.storage import S3FileStore
"""

from __future__ import annotations

from raavan.core.storage.base import FileRef, FileStore
from raavan.core.storage.tenant import FileScope, TenantContext
from raavan.core.storage.local import LocalFileStore
from raavan.core.storage.encrypted import (
    EncryptedFileStore,
    KeyProvider,
    LocalKeyProvider,
)

__all__ = [
    "FileRef",
    "FileStore",
    "FileScope",
    "TenantContext",
    "LocalFileStore",
    "EncryptedFileStore",
    "KeyProvider",
    "LocalKeyProvider",
]
