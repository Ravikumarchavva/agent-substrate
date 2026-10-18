"""Stand in for a conversation's file records in ``_build_file_context`` tests.

The records are objects in the owner's folder (``file_records.py``); these tests are about what the chat does with a
file's state (staged, failed, cached), not about the folder, so they hand the function the records directly. ``rows_db`` returns a
``db`` whose ``commit`` is awaited once for every record the chat saves back, so "was the cache written?" reads as it always did.
``conftest`` restores the real index after each test.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from substrate_cloud.file_records import FileIndex


def rows_db(rows: list) -> MagicMock:
    saved = AsyncMock()

    async def for_thread(self, thread_id, extra=None):
        return list(rows)

    async def save(self, record):
        await saved(record)

    FileIndex.for_thread = for_thread  # type: ignore[method-assign]
    FileIndex.save = save  # type: ignore[method-assign]
    db = MagicMock()
    db.commit = saved
    return db
