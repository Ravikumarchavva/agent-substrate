"""Conversation Service — SQLAlchemy models.

Extracted from monolith server/models.py. Each model maps to
the same Postgres table names for data continuity.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agent_framework.shared.database.base import ServiceBase


class Thread(ServiceBase):
    __tablename__ = "threads"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    user_identifier: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    tags: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String), default=list)
    metadata_: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    steps: Mapped[List["Step"]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="Step.created_at",
    )
    feedbacks: Mapped[List["Feedback"]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Thread(id={self.id}, name={self.name!r})>"


class Step(ServiceBase):
    """Each step in a conversation thread.

    type values:
      - "user_message"      – user input
      - "assistant_message"  – LLM response
      - "tool_call"          – function/tool invocation
      - "tool_result"        – tool execution result
      - "system_message"     – system instructions
    """

    __tablename__ = "steps"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String, nullable=False, default="")
    type: Mapped[str] = mapped_column(String, nullable=False)
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Content
    input: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # State
    streaming: Mapped[bool] = mapped_column(Boolean, default=False)
    wait_for_answer: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    is_error: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    # Metadata
    metadata_: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
    )
    generation: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    start_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    end_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # Relationships
    thread: Mapped["Thread"] = relationship(back_populates="steps")

    def __repr__(self) -> str:
        return f"<Step(id={self.id}, type={self.type!r}, thread={self.thread_id})>"


class Feedback(ServiceBase):
    __tablename__ = "feedbacks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    for_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # Relationships
    thread: Mapped["Thread"] = relationship(back_populates="feedbacks")

    def __repr__(self) -> str:
        return f"<Feedback(id={self.id}, value={self.value})>"
