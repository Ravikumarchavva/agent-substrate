"""Loamz Data's embeddable engine: SQL on SQLite or Postgres, files, vectors, text search, memory and documents.

from loamz.engine import Storage
storage = Storage.at("./.loamz")          # or Storage(postgres(dsn, schema=...), "/var/lib/loamz")
await storage.files.upload(...); await storage.memory.save(...); await storage.vectors.search(...)

from loamz.engine.documents import Library, Reader
library = Library(storage.database, storage.files)
"""

from loamz.engine.host import Storage

__all__ = ["Storage"]
