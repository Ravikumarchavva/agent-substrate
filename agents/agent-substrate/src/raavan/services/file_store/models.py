"""File Store Service — SQLAlchemy models.

File metadata model for tracking uploaded/generated artifacts.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from raavan.shared.database.base import ServiceBase


class FileMetadata(ServiceBase):
    """Metadata for an uploaded or generated file."""

    __tablename__ = "file_metadata"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    original_name: Mapped[str] = mapped_column(String, nullable=False)
    storage_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    content_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    storage_backend: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="local",
    )
    # "local", "s3", "encrypted"

    # Metadata
    metadata_: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
    )
    uploaded_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"<FileMetadata(id={self.id}, name={self.original_name!r})>"
