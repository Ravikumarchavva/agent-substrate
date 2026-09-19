"""PostgreSQL-backed WorkspaceStore implementing branch-isolated workspace snapshot management."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    delete,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from substrate.kernel.exceptions import SnapshotConflictError
from substrate.kernel.storage.snapshots import (
    WorkspaceFileEntry,
    WorkspaceManifest,
    WorkspaceSnapshot,
    WorkspaceStore,
)
from substrate.logger import setup_logging

logger = setup_logging()


class WorkspaceSnapshotBase(DeclarativeBase):
    """Declarative base for workspace snapshot tables."""

    pass


class SnapshotRecord(WorkspaceSnapshotBase):
    """Persisted workspace snapshot record."""

    __tablename__ = "workspace_snapshots"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    branch_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    parent_snapshot_id: Mapped[Optional[str]] = mapped_column(
        String(128),
        ForeignKey("workspace_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    manifest: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    manifest_ref: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class BranchSnapshotHead(WorkspaceSnapshotBase):
    """Active head snapshot pointer for a session branch."""

    __tablename__ = "workspace_branch_heads"
    __table_args__ = (
        UniqueConstraint("session_id", "branch_id", name="uq_workspace_branch_head"),
    )

    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    branch_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("workspace_snapshots.id", ondelete="CASCADE"),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


def _snapshot_from_row(row: SnapshotRecord) -> WorkspaceSnapshot:
    manifest_obj: WorkspaceManifest | None = None
    if row.manifest is not None:
        manifest_obj = WorkspaceManifest.model_validate(row.manifest)

    created = row.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)

    return WorkspaceSnapshot(
        id=row.id,
        session_id=row.session_id,
        branch_id=row.branch_id,
        parent_snapshot_id=row.parent_snapshot_id,
        manifest=manifest_obj,
        manifest_ref=row.manifest_ref,
        created_at=created,
    )


class PostgresWorkspaceStore(WorkspaceStore):
    """PostgreSQL-backed WorkspaceStore providing transactional snapshot branching and CAS checks."""

    def __init__(self, database_url: str, *, echo: bool = False) -> None:
        self._database_url = database_url
        self._echo = echo
        self._engine: Optional[AsyncEngine] = None
        self._session_factory: Optional[async_sessionmaker[AsyncSession]] = None

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
            await conn.run_sync(WorkspaceSnapshotBase.metadata.create_all)
        logger.info("PostgresWorkspaceStore connected and tables ensured")

    async def disconnect(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None
            logger.info("PostgresWorkspaceStore disconnected")

    def _get_session(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            raise RuntimeError(
                "PostgresWorkspaceStore not connected. Call await connect() first."
            )
        return self._session_factory

    async def get_snapshot(self, snapshot_id: str) -> WorkspaceSnapshot | None:
        factory = self._get_session()
        async with factory() as db:
            row = await db.get(SnapshotRecord, snapshot_id)
            if row is None:
                return None
            return _snapshot_from_row(row)

    async def get_branch_snapshot_head(
        self, session_id: str, branch_id: str
    ) -> WorkspaceSnapshot | None:
        factory = self._get_session()
        async with factory() as db:
            head = await db.get(BranchSnapshotHead, (session_id, branch_id))
            if head is None:
                return None
            row = await db.get(SnapshotRecord, head.snapshot_id)
            if row is None:
                return None
            return _snapshot_from_row(row)

    async def commit_snapshot(
        self,
        session_id: str,
        branch_id: str,
        new_snapshot: WorkspaceSnapshot,
        *,
        expected_parent_snapshot_id: str | None,
    ) -> WorkspaceSnapshot:
        if new_snapshot.session_id != session_id:
            raise ValueError(
                f"Snapshot session '{new_snapshot.session_id}' does not match '{session_id}'"
            )
        if new_snapshot.branch_id != branch_id:
            raise ValueError(
                f"Snapshot branch '{new_snapshot.branch_id}' does not match '{branch_id}'"
            )
        if new_snapshot.parent_snapshot_id != expected_parent_snapshot_id:
            raise ValueError(
                f"Snapshot parent '{new_snapshot.parent_snapshot_id}' does not match expected '{expected_parent_snapshot_id}'"
            )

        factory = self._get_session()
        async with factory() as db:
            stmt = (
                select(BranchSnapshotHead)
                .where(
                    BranchSnapshotHead.session_id == session_id,
                    BranchSnapshotHead.branch_id == branch_id,
                )
                .with_for_update()
            )
            res = await db.execute(stmt)
            head = res.scalar_one_or_none()

            current_head_id = head.snapshot_id if head is not None else None

            # Concurrency check
            if current_head_id != expected_parent_snapshot_id:
                raise SnapshotConflictError(
                    f"Conflict committing snapshot on branch '{branch_id}': "
                    f"expected parent '{expected_parent_snapshot_id}', actual current head is '{current_head_id}'",
                    session_id=session_id,
                    branch_id=branch_id,
                    expected_parent_id=expected_parent_snapshot_id,
                    actual_parent_id=current_head_id,
                )

            # Persist snapshot record
            manifest_dict = (
                new_snapshot.manifest.model_dump(mode="json")
                if new_snapshot.manifest is not None
                else None
            )
            record = SnapshotRecord(
                id=new_snapshot.id,
                session_id=session_id,
                branch_id=branch_id,
                parent_snapshot_id=new_snapshot.parent_snapshot_id,
                manifest=manifest_dict,
                manifest_ref=new_snapshot.manifest_ref,
                created_at=new_snapshot.created_at,
            )
            db.add(record)
            await db.flush()

            # Update or create head pointer
            if head is None:
                head = BranchSnapshotHead(
                    session_id=session_id,
                    branch_id=branch_id,
                    snapshot_id=new_snapshot.id,
                )
                db.add(head)
            else:
                head.snapshot_id = new_snapshot.id

            await db.commit()
            return new_snapshot

    async def fork_branch_snapshot(
        self,
        session_id: str,
        source_branch_id: str,
        new_branch_id: str,
    ) -> WorkspaceSnapshot | None:
        factory = self._get_session()
        async with factory() as db:
            existing = await db.get(BranchSnapshotHead, (session_id, new_branch_id))
            if existing is not None:
                raise ValueError(
                    f"Branch '{new_branch_id}' already has a snapshot pointer in session '{session_id}'"
                )

            source_head = await db.get(
                BranchSnapshotHead, (session_id, source_branch_id)
            )
            if source_head is None:
                return None

            new_head = BranchSnapshotHead(
                session_id=session_id,
                branch_id=new_branch_id,
                snapshot_id=source_head.snapshot_id,
            )
            db.add(new_head)
            await db.commit()

            snapshot_row = await db.get(SnapshotRecord, source_head.snapshot_id)
            if snapshot_row is None:
                return None
            return _snapshot_from_row(snapshot_row)

    async def list_snapshots(
        self, session_id: str, branch_id: str | None = None
    ) -> list[WorkspaceSnapshot]:
        factory = self._get_session()
        async with factory() as db:
            stmt = select(SnapshotRecord).where(SnapshotRecord.session_id == session_id)
            if branch_id is not None:
                stmt = stmt.where(SnapshotRecord.branch_id == branch_id)
            stmt = stmt.order_by(SnapshotRecord.created_at)

            result = await db.execute(stmt)
            return [_snapshot_from_row(r) for r in result.scalars().all()]

    async def clear_session(self, session_id: str) -> None:
        """Helper for test cleanup."""
        factory = self._get_session()
        async with factory() as db:
            await db.execute(
                delete(BranchSnapshotHead).where(
                    BranchSnapshotHead.session_id == session_id
                )
            )
            await db.execute(
                delete(SnapshotRecord).where(SnapshotRecord.session_id == session_id)
            )
            await db.commit()


__all__ = [
    "PostgresWorkspaceStore",
    "WorkspaceSnapshotBase",
    "SnapshotRecord",
    "BranchSnapshotHead",
]

