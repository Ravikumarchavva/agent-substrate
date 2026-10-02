"""Object storage (L2) — S3-compatible.

The store's own files and workspaces are ``Store.files`` and ``substrate.workspace.Workspaces``, on a folder or on
PostgreSQL (``integrations.database.postgres_store``). This package holds the backend with a real L2 dependency:
``S3Connector`` (raw S3-compatible client, SeaweedFS by default) and ``S3FileStore`` (the ``FileStore`` implementation
built on top of it, for file contents shared by workers on several machines).
"""

from substrate.integrations.storage.s3_connector import S3Connector
from substrate.integrations.storage.s3 import S3FileStore

__all__ = [
    "S3Connector",
    "S3FileStore",
]
