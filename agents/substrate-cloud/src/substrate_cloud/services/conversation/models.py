"""Conversation Service: SQLAlchemy models.

A thread here is the conversation's header: who owns it and what it is called. What was said is the runtime's run journal, the one
record the monolith also reads (``stream/history.py``); there is no second table of messages to drift from it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from substrate_cloud.shared.database.base import Json, ServiceBase

Tags = JSON().with_variant(ARRAY(String()), "postgresql")


class Thread(ServiceBase):
    __tablename__ = "threads"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # The organization and user of the token that created it. Both are always set; every lookup is under them.
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_identifier: Mapped[str] = mapped_column(String, nullable=False, index=True)
    tags: Mapped[Optional[List[str]]] = mapped_column(Tags, default=list)
    metadata_: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        "metadata", Json, default=dict
    )
    pinned_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    archived_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Deleting hides a thread from its owner; the row stays for an administrator's review (erasure is a separate act).
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<Thread(id={self.id}, name={self.name!r})>"


class Feedback(ServiceBase):
    __tablename__ = "feedbacks"
    __table_args__ = (
        UniqueConstraint("thread_id", "for_id", name="uq_feedbacks_thread_answer"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    for_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    thread_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("threads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<Feedback(id={self.id}, value={self.value})>"
