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
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
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
        back_populates="thread",
        cascade="all, delete-orphan",
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

    # Binary content (images, files stored directly in DB)
    content: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)

    # Relationships
    thread: Mapped[Optional["Thread"]] = relationship(back_populates="elements")

    def __repr__(self) -> str:
        return f"<Element(id={self.id}, name={self.name!r}, type={self.type!r})>"


# ── File Metadata (external storage) ────────────────────────────────────────


class FileMetadata(Base):
    """Metadata for files stored in the external FileStore.

    The actual bytes live in LocalFileStore / S3 / Azure — this table
    tracks ownership, location (object_key), encryption state, and
    soft-deletion.
    """

    __tablename__ = "file_metadata"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Tenant isolation columns
    org_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    thread_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("threads.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    scope: Mapped[str] = mapped_column(String, nullable=False, default="uploads")

    # Storage location
    object_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    original_name: Mapped[str] = mapped_column(String, nullable=False)
    content_type: Mapped[str] = mapped_column(
        String, nullable=False, default="application/octet-stream"
    )
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checksum_sha256: Mapped[str] = mapped_column(String, nullable=False, default="")

    # Encryption
    encryption_mode: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )  # "none", "aes-256-gcm-envelope"
    encrypted_dek: Mapped[Optional[bytes]] = mapped_column(
        LargeBinary, nullable=True
    )  # wrapped DEK (only when envelope encryption is active)

    # Extensible properties
    props: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # soft delete

    def __repr__(self) -> str:
        return (
            f"<FileMetadata(id={self.id}, name={self.original_name!r}, "
            f"key={self.object_key!r})>"
        )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


# ── Feedbacks ────────────────────────────────────────────────────────────────


class Feedback(Base):
    """User feedback on a specific step (thumbs up/down, rating, comment)."""

    __tablename__ = "feedbacks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    for_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("threads.id", ondelete="CASCADE"), nullable=False
    )
    value: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    thread: Mapped["Thread"] = relationship(back_populates="feedbacks")

    def __repr__(self) -> str:
        return f"<Feedback(id={self.id}, value={self.value})>"


# ── Pipelines (Visual Builder) ──────────────────────────────────────────────


class Pipeline(Base):
    """A visual-builder pipeline graph (JSON config stored in JSONB)."""

    __tablename__ = "pipelines"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(
        String, nullable=False, default="Untitled Pipeline"
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    config: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    runs: Mapped[List["PipelineRun"]] = relationship(
        back_populates="pipeline",
        cascade="all, delete-orphan",
        order_by="PipelineRun.started_at.desc()",
    )

    def __repr__(self) -> str:
        return f"<Pipeline(id={self.id}, name={self.name!r})>"


class PipelineRun(Base):
    """A single execution of a pipeline (tracks status and result)."""

    __tablename__ = "pipeline_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pipeline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pipelines.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    input_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    pipeline: Mapped["Pipeline"] = relationship(back_populates="runs")

    def __repr__(self) -> str:
        return f"<PipelineRun(id={self.id}, status={self.status!r})>"


class AdapterPipeline(Base):
    """A saved adapter chain definition (YAML/JSON pipeline)."""

    __tablename__ = "adapter_pipelines"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    definition_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )

    def __repr__(self) -> str:
        return f"<AdapterPipeline(id={self.id}, name={self.name!r})>"
