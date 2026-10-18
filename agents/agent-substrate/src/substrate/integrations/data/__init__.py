"""substrate-data adapters (the ``agent-substrate[data]`` extra): governed files for the agent runtime.

Nothing here is imported by the engine; the platform picks it when a data service is configured (ADR 0004).
"""

from substrate.integrations.data._errors import FileConflictError, FileTooLargeError
from substrate.integrations.data._keys import default_volume, tenant_alias
from substrate.integrations.data.files import (
    DataFilesConnection,
    DataFileStore,
    KeyScopedFileStore,
    TokenProvider,
    UploadTicket,
)
from substrate.integrations.data.sessions import DataUploadSessions

__all__ = [
    "DataFileStore",
    "DataFilesConnection",
    "DataUploadSessions",
    "FileConflictError",
    "FileTooLargeError",
    "KeyScopedFileStore",
    "TokenProvider",
    "UploadTicket",
    "default_volume",
    "tenant_alias",
]
