"""File Store Service — business logic.

Handles file upload, download, listing, and deletion using
pluggable storage backends (local, S3, encrypted).
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_framework.services.file_store.models import FileMetadata

logger = logging.getLogger(__name__)


async def create_file_record(
    db: AsyncSession,
    *,
    thread_id: uuid.UUID,
    original_name: str,
    storage_key: str,
    content_type: Optional[str] = None,
    size_bytes: int = 0,
    storage_backend: str = "local",
    metadata: Optional[Dict[str, Any]] = None,
    uploaded_by: Optional[str] = None,
) -> FileMetadata:
    """Create a file metadata record."""
    record = FileMetadata(
        thread_id=thread_id,
        original_name=original_name,
        storage_key=storage_key,
        content_type=content_type,
        size_bytes=size_bytes,
        storage_backend=storage_backend,
        metadata_=metadata or {},
        uploaded_by=uploaded_by,
    )
    db.add(record)
    await db.flush()
    return record


async def get_file(db: AsyncSession, file_id: uuid.UUID) -> Optional[FileMetadata]:
    result = await db.execute(
        select(FileMetadata).where(FileMetadata.id == file_id)
    )
    return result.scalar_one_or_none()


async def list_files(
    db: AsyncSession,
    thread_id: uuid.UUID,
) -> List[FileMetadata]:
    result = await db.execute(
        select(FileMetadata)
        .where(FileMetadata.thread_id == thread_id)
        .order_by(FileMetadata.created_at)
    )
    return list(result.scalars().all())


async def delete_file(db: AsyncSession, file_id: uuid.UUID) -> bool:
    result = await db.execute(
        delete(FileMetadata).where(FileMetadata.id == file_id)
    )
    return result.rowcount > 0


async def get_files_by_ids(
    db: AsyncSession,
    file_ids: List[uuid.UUID],
    thread_id: uuid.UUID,
) -> List[FileMetadata]:
    """Get multiple files by ID, scoped to a thread."""
    result = await db.execute(
        select(FileMetadata)
        .where(
            FileMetadata.id.in_(file_ids),
            FileMetadata.thread_id == thread_id,
        )
    )
    return list(result.scalars().all())
