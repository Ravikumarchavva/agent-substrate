"""SQLAlchemy ORM models for the chat server.

Schema inspired by Chainlit's data layer, adapted for agent-framework.

Tables:
  users     – authenticated users
  threads   – chat sessions / conversations
  steps     – each agent step (LLM call, tool call, message)
  elements  – file attachments, images, etc.
  feedbacks – user ratings on messages
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""
    pass


# ── Users ────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    identifier: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    metadata_: Mapped[Dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    threads: Mapped[List["Thread"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, identifier={self.identifier!r})>"


# ── Threads (Sessions) ──────────────────────────────────────────────────────

class Thread(Base):
    __tablename__ = "threads"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    user_identifier: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    tags: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String), default=list)
    metadata_: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        "metadata", JSONB, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    user: Mapped[Optional["User"]] = relationship(back_populates="threads")
    steps: Mapped[List["Step"]] = relationship(
        back_populates="thread", cascade="all, delete-orphan",
        order_by="Step.created_at",
    )
    elements: Mapped[List["Element"]] = relationship(
        back_populates="thread", cascade="all, delete-orphan"
    )
    feedbacks: Mapped[List["Feedback"]] = relationship(
        back_populates="thread", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Thread(id={self.id}, name={self.name!r})>"


# ── Steps (Messages / Tool Calls / Agent Steps) ─────────────────────────────

class Step(Base):
    """Each step in a conversation thread.

    Covers user messages, assistant messages, tool calls, tool results,
    and internal agent steps.
    
    type values:
      - "user_message"   – user input
      - "assistant_message" – LLM response
      - "tool_call"       – function/tool invocation
      - "tool_result"     – tool execution result
      - "system_message"  – system instructions
    """
    __tablename__ = "steps"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String, nullable=False, default="")
    type: Mapped[str] = mapped_column(String, nullable=False)
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("threads.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
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
        "metadata", JSONB, default=dict
    )
    tags: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String), default=list)
    generation: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    start_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    end_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Display
    show_input: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    language: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    indent: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Relationships
    thread: Mapped["Thread"] = relationship(back_populates="steps")

    def __repr__(self) -> str:
        return f"<Step(id={self.id}, type={self.type!r}, name={self.name!r})>"


# ── Elements (Attachments) ───────────────────────────────────────────────────

class Element(Base):
    """File attachments, images, audio, or other media linked to a thread."""
    __tablename__ = "elements"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    thread_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("threads.id", ondelete="CASCADE"), nullable=True
    )
    type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    display: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    object_key: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    size: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    page: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    language: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    for_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    mime: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    props: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    # Relationships
    thread: Mapped[Optional["Thread"]] = relationship(back_populates="elements")

    def __repr__(self) -> str:
        return f"<Element(id={self.id}, name={self.name!r}, type={self.type!r})>"


# ── Feedbacks ────────────────────────────────────────────────────────────────

class Feedback(Base):
    """User feedback on a specific step (thumbs up/down, rating, comment)."""
    __tablename__ = "feedbacks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    for_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("threads.id", ondelete="CASCADE"), nullable=False
    )
    value: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    thread: Mapped["Thread"] = relationship(back_populates="feedbacks")

    def __repr__(self) -> str:
        return f"<Feedback(id={self.id}, value={self.value})>"
