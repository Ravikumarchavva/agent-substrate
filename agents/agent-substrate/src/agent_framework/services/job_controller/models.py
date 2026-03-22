"""Job Controller — SQLAlchemy models.

JobRun tracks the lifecycle of a single agent invocation and
provides durable state for cancellation, retry, and observability.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from agent_framework.shared.database.base import ServiceBase


class JobRun(ServiceBase):
    """Tracks a single chat-to-completion job run."""

    __tablename__ = "job_runs"

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
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    # Idempotency key from client
    client_request_id: Mapped[Optional[str]] = mapped_column(
        String,
        nullable=True,
        unique=True,
    )
    status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="pending",
    )
    # Status transitions: pending → running → completed|failed|cancelled

    # Input
    user_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    system_instructions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_ids: Mapped[Optional[list]] = mapped_column(JSONB, default=list)

    # Output
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    steps_count: Mapped[int] = mapped_column(Integer, default=0)

    # Metadata
    metadata_: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:
        return f"<JobRun(id={self.id}, thread={self.thread_id}, status={self.status}))>"
