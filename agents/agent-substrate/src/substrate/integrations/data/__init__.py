"""substrate-data adapters (the ``agent-substrate[data]`` extra): governed files, documents and memory for the agent runtime.

Nothing here is imported by the engine; the platform picks it when a data service is configured (ADR 0004).
"""

from substrate.integrations.data._connection import (
    DataServiceConnection,
    DocumentTokens,
    identity_of,
)
from substrate.integrations.data._errors import FileConflictError, FileTooLargeError
from substrate.integrations.data._keys import default_volume, tenant_alias
from substrate.integrations.data.files import (
    DataFilesConnection,
    DataFileStore,
    KeyScopedFileStore,
    TokenProvider,
    UploadTicket,
)
from substrate.integrations.data.library import DataLibrary
from substrate.integrations.data.memory import DataMemoryStore, MemoryTokens
from substrate.integrations.data.sessions import DataUploadSessions

__all__ = [
    "DataLibrary",
    "DataMemoryStore",
    "DataServiceConnection",
    "DocumentTokens",
    "MemoryTokens",
    "identity_of",
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
