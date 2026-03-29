"""Human Gate Service — SQLAlchemy models.

Tracks pending and resolved HITL approval/input requests.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from raavan.shared.database.base import ServiceBase


class HITLRequest(ServiceBase):
    """Tracks a pending or resolved HITL request."""

    __tablename__ = "hitl_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    request_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
        unique=True,
        index=True,
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    run_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    type: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )
    # type: "tool_approval" or "human_input"

    # Request data
    tool_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    tool_input: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    options: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    # Resolution
    status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="pending",
    )
    # Status: pending → approved|rejected|answered|timeout|cancelled
    response_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    responded_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:
        return f"<HITLRequest(id={self.request_id}, type={self.type}, status={self.status})>"
