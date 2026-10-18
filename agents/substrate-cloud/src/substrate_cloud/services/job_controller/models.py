"""Job Controller: SQLAlchemy models.

JobRun tracks the lifecycle of a single agent invocation and provides durable state for cancellation and observability.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from substrate_cloud.shared.database.base import Json, ServiceBase


class JobRun(ServiceBase):
    """Tracks a single chat-to-completion job run."""

    __tablename__ = "job_runs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id",
            "client_request_id",
            name="uq_job_runs_client_request",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    thread_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    # Who the run is for: the organization and user of the token that started it, never a value from a request body.
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    # The runtime's own id for the run (its journal), known once the agent runtime has accepted it.
    runtime_run_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Idempotency key from the client
    client_request_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # pending -> running -> completed | failed | cancelled
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")

    user_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    system_instructions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_ids: Mapped[Optional[list]] = mapped_column(Json, default=list)

    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    steps_count: Mapped[int] = mapped_column(Integer, default=0)
    metadata_: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        "metadata", Json, default=dict
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<JobRun(id={self.id}, thread={self.thread_id}, status={self.status})>"
