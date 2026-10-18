"""Loamz Data's embeddable engine: SQL on SQLite or Postgres, files, vectors, text search, memory and documents.

from loamz.data.storage import Storage
storage = Storage.at("./.loamz")          # or Storage(postgres(dsn, schema=...), "/var/lib/loamz")
await storage.files.upload(...); await storage.memory.save(...); await storage.vectors.search(...)

from loamz.data.storage.documents import Library, Reader
library = Library(storage.database, storage.files)
"""

from loamz.data.storage.host import Storage

__all__ = ["Storage"]
