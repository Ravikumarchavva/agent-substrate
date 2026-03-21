"""Pluggable file-storage layer.

Provides a backend-agnostic ``FileStore`` ABC with drivers for local
filesystem, S3-compatible object stores, and an optional encryption wrapper.

Public surface::

    from agent_framework.core.storage import (
        FileStore,          # ABC
        FileRef,            # returned by put / get
        TenantContext,      # org/user/thread path builder
        LocalFileStore,     # local-disk driver
        S3FileStore,        # S3-compatible driver
        EncryptedFileStore, # envelope-encryption decorator
    )
"""
from __future__ import annotations

from agent_framework.core.storage.base import FileRef, FileStore
from agent_framework.core.storage.tenant import FileScope, TenantContext
from agent_framework.core.storage.local import LocalFileStore
from agent_framework.core.storage.s3 import S3FileStore
from agent_framework.core.storage.encrypted import EncryptedFileStore, KeyProvider, LocalKeyProvider

__all__ = [
    "FileRef",
    "FileStore",
    "FileScope",
    "TenantContext",
    "LocalFileStore",
    "S3FileStore",
    "EncryptedFileStore",
    "KeyProvider",
    "LocalKeyProvider",
]
