"""Keyed bytes: the ``FileStore`` contract and ``Files``, its database-and-folder implementation."""

from loamz.data.storage.files.contract import FileStore
from loamz.data.storage.files.tables import Files

__all__ = ["FileStore", "Files"]
