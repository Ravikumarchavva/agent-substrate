"""DurableHistoryProvider — PostgreSQL-backed durable conversation history.

Durable, queryable persistence for session messages and conversation DAGs using SQLAlchemy 2.0 async ORM.

Tables (created automatically):
  ``history_sessions``     — legacy session tracking (timestamps, message count).
  ``history_messages``     — legacy message storage within a session.
  ``history_nodes``        — immutable DAG nodes with parent pointers and ChatMessage payloads.
  ``history_branches``     — movable branch head pointers with optimistic concurrency control.
  ``history_checkpoints``  — compaction checkpoint records referencing anchor nodes.

Security:
  - All queries use parameterized ORM operations — no raw SQL interpolation.
  - Raw session IDs are validated at the public protocol boundary.
"""

from __future__ import annotations

import base64
import hashlib
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    delete,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from substrate.kernel.core.content import ChatMessage
from substrate.kernel.core.identity import Actor
from substrate.kernel.exceptions import (
    BranchAlreadyExistsError,
    BranchHeadConflictError,
    BranchNotFoundError,
    DAGIntegrityError,
)
from substrate.kernel.storage.history import (
    Branch,
    HistoryCheckpoint,
    HistoryProvider,
    MessageNode,
)
from substrate.logger import setup_logging

logger = setup_logging()

_UNSET: Any = object()


# ── Message serialisation (provider-agnostic JSON round-trip) ─────────────────


def _bytes_to_b64(val: Any) -> Any:
    if isinstance(val, dict):
        return {k: _bytes_to_b64(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_bytes_to_b64(x) for x in val]
    if isinstance(val, bytes):
        return {"__bytes_b64__": base64.b64encode(val).decode("utf-8")}
    return val


def _b64_to_bytes(val: Any) -> Any:
    if isinstance(val, dict):
        if "__bytes_b64__" in val:
            return base64.b64decode(val["__bytes_b64__"])
        return {k: _b64_to_bytes(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_b64_to_bytes(x) for x in val]
    return val


def serialize_message(message: ChatMessage) -> Dict[str, Any]:
    """Serialize a ChatMessage to a dict suitable for JSONB storage."""
    return _bytes_to_b64(message.model_dump())


def deserialize_message(data: Dict[str, Any]) -> ChatMessage:
    """Deserialize a JSONB dict back to a ChatMessage."""
    return ChatMessage.model_validate(_b64_to_bytes(data))


# ─────────────────────────────────────────────────────────────────────────────

_SESSION_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
_MAX_STORAGE_SESSION_KEY_LENGTH = 128


def _validate_session_id(session_id: str) -> None:
    if not _SESSION_ID_PATTERN.match(session_id):
        raise ValueError(
            f"Invalid session_id: must match {_SESSION_ID_PATTERN.pattern}"
        )


# ---------------------------------------------------------------------------
# ORM Models — separate Base from server models to avoid metadata conflicts
# ---------------------------------------------------------------------------


class HistoryBase(DeclarativeBase):
    """Separate declarative base for the history subsystem."""

    pass


class HistorySession(HistoryBase):
    """Persistent session record (legacy)."""

    __tablename__ = "history_sessions"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[List["HistoryMessage"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="HistoryMessage.sequence",
    )

    def __repr__(self) -> str:
        return f"<HistorySession(id={self.id!r}, msgs={self.message_count})>"


class HistoryMessage(HistoryBase):
    """Single message stored for a session (legacy)."""

    __tablename__ = "history_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence", name="uq_history_session_sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True) if hasattr(uuid, "UUID") else String(36),
        primary_key=True,
        default=uuid.uuid4,
    )
    session_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("history_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="", index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    message_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    session: Mapped[HistorySession] = relationship(back_populates="messages")

    def __repr__(self) -> str:
        return (
            f"<HistoryMessage(id={self.id}, session={self.session_id!r}, "
            f"seq={self.sequence}, type={self.message_type!r})>"
        )


class HistoryNode(HistoryBase):
    """Immutable node in the conversation DAG."""

    __tablename__ = "history_nodes"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    parent_id: Mapped[Optional[str]] = mapped_column(
        String(128),
        ForeignKey("history_nodes.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    session_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    payload: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class HistoryBranch(HistoryBase):
    """Movable branch pointer referencing a DAG head node."""

    __tablename__ = "history_branches"
    __table_args__ = (
        UniqueConstraint("session_id", "id", name="uq_history_branch_session_id"),
    )

    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    head_message_id: Mapped[Optional[str]] = mapped_column(
        String(128), ForeignKey("history_nodes.id", ondelete="SET NULL"), nullable=True
    )
    forked_from_message_id: Mapped[Optional[str]] = mapped_column(
        String(128), ForeignKey("history_nodes.id", ondelete="SET NULL"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class HistoryCheckpointRecord(HistoryBase):
    """Compaction checkpoint anchor record."""

    __tablename__ = "history_checkpoints"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    anchor_message_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("history_nodes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    parent_checkpoint_id: Mapped[Optional[str]] = mapped_column(
        String(128),
        ForeignKey("history_checkpoints.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


def _node_from_row(row: HistoryNode) -> MessageNode:
    created = row.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return MessageNode(
        id=row.id,
        parent_id=row.parent_id,
        session_id=row.session_id,
        run_id=row.run_id,
        payload=deserialize_message(row.payload),
        created_at=created,
    )


def _branch_from_row(row: HistoryBranch) -> Branch:
    created = row.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return Branch(
        id=row.id,
        session_id=row.session_id,
        head_message_id=row.head_message_id,
        forked_from_message_id=row.forked_from_message_id,
        version=row.version,
        created_at=created,
    )


def _checkpoint_from_row(row: HistoryCheckpointRecord) -> HistoryCheckpoint:
    created = row.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return HistoryCheckpoint(
        id=row.id,
        session_id=row.session_id,
        anchor_message_id=row.anchor_message_id,
        summary=row.summary,
        state=row.state,
        parent_checkpoint_id=row.parent_checkpoint_id,
        created_at=created,
    )


# ---------------------------------------------------------------------------
# DurableHistoryProvider
# ---------------------------------------------------------------------------


class DurableHistoryProvider:
    """Async PostgreSQL-backed history provider supporting both DAG and legacy modes.

    Parameters:
        database_url: PostgreSQL connection string
            (e.g. ``postgresql+asyncpg://user:pass@localhost/agentdb``).
        echo: If ``True``, log all SQL statements.
    """

    def __init__(self, database_url: str, *, echo: bool = False) -> None:
        self._database_url = database_url
        self._echo = echo
        self._engine: Optional[AsyncEngine] = None
        self._session_factory: Optional[async_sessionmaker[AsyncSession]] = None

    # -- Lifecycle ------------------------------------------------------------

    async def connect(self) -> None:
        if self._engine is not None:
            return
        self._engine = create_async_engine(
            self._database_url,
            echo=self._echo,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
        )
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with self._engine.begin() as conn:
            await conn.run_sync(HistoryBase.metadata.create_all)
        logger.info("DurableHistoryProvider connected and tables ensured")

    async def disconnect(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None
            logger.info("DurableHistoryProvider disconnected")

    def _get_session(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            raise RuntimeError(
                "DurableHistoryProvider not connected. Call await connect() first."
            )
        return self._session_factory

    # ── DAG Node & Branch Operations ──────────────────────────────────────────

    async def _append_node_in_tx(self, db: AsyncSession, node: MessageNode) -> None:
        if node.parent_id is not None and node.parent_id == node.id:
            raise DAGIntegrityError(f"Node '{node.id}' cannot have itself as parent")

        existing = await db.get(HistoryNode, node.id)
        if existing is not None:
            existing_payload = deserialize_message(existing.payload).model_dump(mode="json")
            node_payload = node.payload.model_dump(mode="json")
            is_identical = (
                existing.parent_id == node.parent_id
                and existing.session_id == node.session_id
                and existing.run_id == node.run_id
                and existing_payload == node_payload
            )
            if is_identical:
                return
            raise DAGIntegrityError(
                f"Node '{node.id}' already exists with different contents"
            )

        if node.parent_id is not None:
            parent = await db.get(HistoryNode, node.parent_id)
            if parent is None:
                raise DAGIntegrityError(
                    f"Parent node '{node.parent_id}' does not exist"
                )
            if parent.session_id != node.session_id:
                raise DAGIntegrityError(
                    f"Parent node belongs to session '{parent.session_id}', expected '{node.session_id}'"
                )

        record = HistoryNode(
            id=node.id,
            parent_id=node.parent_id,
            session_id=node.session_id,
            run_id=node.run_id,
            payload=serialize_message(node.payload),
            created_at=node.created_at,
        )
        db.add(record)
        await db.flush()

    async def append_node(self, node: MessageNode) -> None:
        """Persist an immutable node."""
        factory = self._get_session()
        async with factory() as db:
            await self._append_node_in_tx(db, node)
            await db.commit()

    async def get_node(self, node_id: str) -> MessageNode | None:
        """Fetch a single node by its ID."""
        factory = self._get_session()
        async with factory() as db:
            row = await db.get(HistoryNode, node_id)
            if row is None:
                return None
            return _node_from_row(row)

    async def get_branch(self, session_id: str, branch_id: str) -> Branch | None:
        """Fetch a branch head pointer by session and branch ID."""
        factory = self._get_session()
        async with factory() as db:
            row = await db.get(HistoryBranch, (session_id, branch_id))
            if row is None:
                return None
            return _branch_from_row(row)

    async def ensure_branch(
        self, session_id: str, branch_id: str, *, head_message_id: str | None = None
    ) -> Branch:
        """Fetch or initialize a branch."""
        factory = self._get_session()
        async with factory() as db:
            row = await db.get(HistoryBranch, (session_id, branch_id))
            if row is None:
                row = HistoryBranch(
                    id=branch_id,
                    session_id=session_id,
                    head_message_id=head_message_id,
                    version=0,
                )
                db.add(row)
                await db.commit()
            return _branch_from_row(row)

    async def fork_branch(
        self,
        session_id: str,
        source_branch_id: str,
        new_branch_id: str,
        *,
        fork_from_message_id: str | None = None,
    ) -> Branch:
        """Atomically fork a new branch pointer from source branch or an ancestor node."""
        factory = self._get_session()
        async with factory() as db:
            existing = await db.get(HistoryBranch, (session_id, new_branch_id))
            if existing is not None:
                raise BranchAlreadyExistsError(
                    f"Branch '{new_branch_id}' already exists in session '{session_id}'"
                )

            source_branch = await db.get(HistoryBranch, (session_id, source_branch_id))
            if source_branch is None:
                raise BranchNotFoundError(
                    f"Source branch '{source_branch_id}' not found in session '{session_id}'"
                )

            # Empty source branch handling
            if source_branch.head_message_id is None:
                if fork_from_message_id is not None:
                    raise DAGIntegrityError(
                        f"Cannot fork from node '{fork_from_message_id}' on empty branch '{source_branch_id}'"
                    )
                new_branch = HistoryBranch(
                    id=new_branch_id,
                    session_id=session_id,
                    head_message_id=None,
                    forked_from_message_id=None,
                    version=0,
                )
                db.add(new_branch)
                await db.commit()
                return _branch_from_row(new_branch)

            # Non-empty source branch handling
            if fork_from_message_id is None:
                target_id = source_branch.head_message_id
            else:
                target_node = await db.get(HistoryNode, fork_from_message_id)
                if target_node is None:
                    raise DAGIntegrityError(
                        f"Fork node '{fork_from_message_id}' does not exist"
                    )
                if target_node.session_id != session_id:
                    raise DAGIntegrityError(
                        f"Fork node belongs to session '{target_node.session_id}', expected '{session_id}'"
                    )

                # Verify target_node is in source branch's ancestry chain
                curr: str | None = source_branch.head_message_id
                found = False
                while curr is not None:
                    if curr == fork_from_message_id:
                        found = True
                        break
                    curr_node = await db.get(HistoryNode, curr)
                    curr = curr_node.parent_id if curr_node else None

                if not found:
                    raise DAGIntegrityError(
                        f"Node '{fork_from_message_id}' is not an ancestor of source branch head '{source_branch.head_message_id}'"
                    )
                target_id = fork_from_message_id

            new_branch = HistoryBranch(
                id=new_branch_id,
                session_id=session_id,
                head_message_id=target_id,
                forked_from_message_id=target_id,
                version=0,
            )
            db.add(new_branch)
            await db.commit()
            return _branch_from_row(new_branch)

    async def set_branch_head(
        self,
        session_id: str,
        branch_id: str,
        new_head_id: str,
        *,
        expected_head_id: str | None = _UNSET,
        expected_version: int | None = None,
    ) -> Branch:
        """Advance branch head pointer using optimistic concurrency control."""
        factory = self._get_session()
        async with factory() as db:
            stmt = (
                select(HistoryBranch)
                .where(
                    HistoryBranch.session_id == session_id,
                    HistoryBranch.id == branch_id,
                )
                .with_for_update()
            )
            res = await db.execute(stmt)
            branch = res.scalar_one_or_none()

            if branch is None:
                raise BranchNotFoundError(
                    f"Branch '{branch_id}' not found in session '{session_id}'"
                )

            # CAS checks
            if expected_head_id is not _UNSET:
                if branch.head_message_id != expected_head_id:
                    raise BranchHeadConflictError(
                        f"Branch head conflict: expected '{expected_head_id}', actual '{branch.head_message_id}'",
                        session_id=session_id,
                        branch_id=branch_id,
                        expected=expected_head_id,
                        actual=branch.head_message_id,
                    )

            if expected_version is not None:
                if branch.version != expected_version:
                    raise BranchHeadConflictError(
                        f"Branch version conflict: expected {expected_version}, actual {branch.version}",
                        session_id=session_id,
                        branch_id=branch_id,
                        expected=expected_version,
                        actual=branch.version,
                    )

            new_head = await db.get(HistoryNode, new_head_id)
            if new_head is None:
                raise DAGIntegrityError(
                    f"New head node '{new_head_id}' does not exist"
                )
            if new_head.session_id != session_id:
                raise DAGIntegrityError(
                    f"New head node belongs to session '{new_head.session_id}', expected '{session_id}'"
                )

            branch.head_message_id = new_head_id
            branch.version = branch.version + 1
            await db.commit()
            return _branch_from_row(branch)

    async def append_and_advance(
        self,
        node: MessageNode,
        branch_id: str,
        *,
        expected_head_id: str | None = _UNSET,
        expected_version: int | None = None,
    ) -> Branch:
        """Atomically append node and advance branch head."""
        factory = self._get_session()
        async with factory() as db:
            stmt = (
                select(HistoryBranch)
                .where(
                    HistoryBranch.session_id == node.session_id,
                    HistoryBranch.id == branch_id,
                )
                .with_for_update()
            )
            res = await db.execute(stmt)
            branch = res.scalar_one_or_none()

            if branch is None:
                branch = HistoryBranch(
                    id=branch_id,
                    session_id=node.session_id,
                    head_message_id=None,
                    version=0,
                )
                db.add(branch)
                await db.flush()

            current_head = branch.head_message_id

            # Invariant: node.parent_id must match current branch head!
            if node.parent_id != current_head:
                raise BranchHeadConflictError(
                    f"Cannot advance branch '{branch_id}': node parent '{node.parent_id}' does not match current head '{current_head}'",
                    session_id=node.session_id,
                    branch_id=branch_id,
                    expected=current_head,
                    actual=node.parent_id,
                )

            # CAS verification
            if expected_head_id is not _UNSET:
                if current_head != expected_head_id:
                    raise BranchHeadConflictError(
                        f"Branch head conflict on advance: expected '{expected_head_id}', actual '{current_head}'",
                        session_id=node.session_id,
                        branch_id=branch_id,
                        expected=expected_head_id,
                        actual=current_head,
                    )

            if expected_version is not None:
                if branch.version != expected_version:
                    raise BranchHeadConflictError(
                        f"Branch version conflict on advance: expected {expected_version}, actual {branch.version}",
                        session_id=node.session_id,
                        branch_id=branch_id,
                        expected=expected_version,
                        actual=branch.version,
                    )

            # Atomic commit: append node + advance head
            await self._append_node_in_tx(db, node)

            branch.head_message_id = node.id
            branch.version = branch.version + 1
            await db.commit()
            return _branch_from_row(branch)

    # ── Checkpoint Operations ────────────────────────────────────────────────

    async def save_checkpoint(self, checkpoint: HistoryCheckpoint) -> None:
        """Persist a compaction checkpoint anchor."""
        factory = self._get_session()
        async with factory() as db:
            anchor = await db.get(HistoryNode, checkpoint.anchor_message_id)
            if anchor is None:
                raise DAGIntegrityError(
                    f"Checkpoint anchor node '{checkpoint.anchor_message_id}' does not exist"
                )
            if anchor.session_id != checkpoint.session_id:
                raise DAGIntegrityError(
                    f"Checkpoint anchor belongs to session '{anchor.session_id}', expected '{checkpoint.session_id}'"
                )

            record = HistoryCheckpointRecord(
                id=checkpoint.id,
                session_id=checkpoint.session_id,
                anchor_message_id=checkpoint.anchor_message_id,
                summary=checkpoint.summary,
                state=checkpoint.state,
                parent_checkpoint_id=checkpoint.parent_checkpoint_id,
                created_at=checkpoint.created_at,
            )
            db.add(record)
            await db.commit()

    async def get_checkpoint(self, checkpoint_id: str) -> HistoryCheckpoint | None:
        """Fetch a checkpoint by ID."""
        factory = self._get_session()
        async with factory() as db:
            row = await db.get(HistoryCheckpointRecord, checkpoint_id)
            if row is None:
                return None
            return _checkpoint_from_row(row)

    async def list_checkpoints(self, session_id: str) -> list[HistoryCheckpoint]:
        """List all checkpoints stored for a session."""
        factory = self._get_session()
        async with factory() as db:
            stmt = (
                select(HistoryCheckpointRecord)
                .where(HistoryCheckpointRecord.session_id == session_id)
                .order_by(HistoryCheckpointRecord.created_at)
            )
            result = await db.execute(stmt)
            return [_checkpoint_from_row(r) for r in result.scalars().all()]

    async def clear_session_dag(self, session_id: str) -> None:
        """Helper to clear DAG entities for a session."""
        factory = self._get_session()
        async with factory() as db:
            await db.execute(
                delete(HistoryCheckpointRecord).where(
                    HistoryCheckpointRecord.session_id == session_id
                )
            )
            await db.execute(
                delete(HistoryBranch).where(HistoryBranch.session_id == session_id)
            )
            await db.execute(
                delete(HistoryNode).where(HistoryNode.session_id == session_id)
            )
            await db.commit()

    # -- HistoryProvider legacy protocol (kernel contract) --------------------

    def _session_key(self, agent_id: Actor, session_id: str) -> str:
        """Derive the internal storage key for a (agent_id, session_id) pair."""
        key = f"{agent_id.type}:{agent_id.key or session_id}"
        if len(key) <= _MAX_STORAGE_SESSION_KEY_LENGTH:
            return key
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return f"h:{digest}"

    async def append(
        self,
        agent_id: Actor,
        message: ChatMessage,
        *,
        session_id: str,
        run_id: str = "",
    ) -> None:
        _validate_session_id(session_id)
        storage_key = self._session_key(agent_id, session_id)
        await self.save_messages(storage_key, [message], run_id=run_id)

    async def append_many(
        self,
        agent_id: Actor,
        messages: list[ChatMessage],
        *,
        session_id: str,
        run_id: str = "",
    ) -> None:
        _validate_session_id(session_id)
        storage_key = self._session_key(agent_id, session_id)
        await self.save_messages(storage_key, messages, run_id=run_id)

    async def get_messages(
        self,
        agent_id: Actor,
        *,
        session_id: str,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[ChatMessage]:
        _validate_session_id(session_id)
        storage_key = self._session_key(agent_id, session_id)
        return await self.load_messages(storage_key, limit=limit, offset=offset)

    async def clear(self, agent_id: Actor, *, session_id: str) -> None:
        _validate_session_id(session_id)
        storage_key = self._session_key(agent_id, session_id)
        await self.clear_session(storage_key)

    async def clear_run(
        self, agent_id: Actor, *, session_id: str, run_id: str
    ) -> None:
        _validate_session_id(session_id)
        storage_key = self._session_key(agent_id, session_id)
        factory = self._get_session()
        async with factory() as db:
            await db.execute(
                delete(HistoryMessage).where(
                    HistoryMessage.session_id == storage_key,
                    HistoryMessage.run_id == run_id,
                )
            )
            session_obj = await db.get(
                HistorySession, storage_key, with_for_update=True
            )
            if session_obj is not None:
                stmt = (
                    select(func.count())
                    .select_from(HistoryMessage)
                    .where(HistoryMessage.session_id == storage_key)
                )
                result = await db.execute(stmt)
                session_obj.message_count = result.scalar_one()
            await db.commit()

    # -- Internal helpers (used by protocol methods above) --------------------

    async def save_messages(
        self, session_id: str, messages: List[ChatMessage], run_id: str = ""
    ) -> int:
        if not messages:
            return 0

        factory = self._get_session()
        async with factory() as db:
            session_obj = await db.get(HistorySession, session_id, with_for_update=True)
            if session_obj is None:
                session_obj = HistorySession(id=session_id, message_count=0)
                db.add(session_obj)
                await db.flush()

            stmt = select(func.coalesce(func.max(HistoryMessage.sequence), 0)).where(
                HistoryMessage.session_id == session_id
            )
            result = await db.execute(stmt)
            max_seq: int = result.scalar_one()

            for i, msg in enumerate(messages, start=max_seq + 1):
                payload = serialize_message(msg)
                db.add(
                    HistoryMessage(
                        session_id=session_id,
                        sequence=i,
                        message_type=payload.get("type", type(msg).__name__),
                        payload=payload,
                        run_id=run_id,
                    )
                )

            session_obj.message_count = max_seq + len(messages)
            await db.commit()
            logger.debug(
                "Saved %d messages for session %s (run_id=%s)",
                len(messages),
                session_id,
                run_id,
            )
            return len(messages)

    async def load_messages(
        self,
        session_id: str,
        *,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> List[ChatMessage]:
        factory = self._get_session()
        async with factory() as db:
            if limit is not None and limit > 0 and offset is None:
                stmt = (
                    select(HistoryMessage)
                    .where(HistoryMessage.session_id == session_id)
                    .order_by(HistoryMessage.sequence.desc())
                    .limit(limit)
                )
                result = await db.execute(stmt)
                rows = list(reversed(result.scalars().all()))
            else:
                stmt = (
                    select(HistoryMessage)
                    .where(HistoryMessage.session_id == session_id)
                    .order_by(HistoryMessage.sequence)
                )
                if offset is not None:
                    stmt = stmt.offset(offset)
                if limit is not None:
                    stmt = stmt.limit(limit)
                result = await db.execute(stmt)
                rows = list(result.scalars().all())

            return [deserialize_message(row.payload) for row in rows]

    async def clear_session(self, session_id: str) -> None:
        factory = self._get_session()
        async with factory() as db:
            await db.execute(
                delete(HistoryMessage).where(HistoryMessage.session_id == session_id)
            )
            session_obj = await db.get(HistorySession, session_id)
            if session_obj is not None:
                session_obj.message_count = 0
            await db.commit()

    async def count_messages(self, agent_id: Actor, *, session_id: str) -> int:
        """Return the number of messages for *agent_id* in *session_id*."""
        _validate_session_id(session_id)
        storage_key = self._session_key(agent_id, session_id)
        return await self._count_by_storage_key(storage_key)

    async def _count_by_storage_key(self, storage_key: str) -> int:
        """Count messages by internal storage key (used by internal helpers)."""
        factory = self._get_session()
        async with factory() as db:
            stmt = (
                select(func.count())
                .select_from(HistoryMessage)
                .where(HistoryMessage.session_id == storage_key)
            )
            result = await db.execute(stmt)
            return result.scalar_one()


__all__ = [
    "DurableHistoryProvider",
    "HistoryBase",
    "HistorySession",
    "HistoryMessage",
    "HistoryNode",
    "HistoryBranch",
    "HistoryCheckpointRecord",
    "serialize_message",
    "deserialize_message",
]
